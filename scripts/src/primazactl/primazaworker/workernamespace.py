from primazactl.utils import logger
from primazactl.kube.namespace import Namespace
from primazactl.kube.role import Role
from primazactl.kube.rolebinding import RoleBinding
from primazactl.kube.roles.primazaroles import get_primaza_namespace_role
from primazactl.primaza.primazacluster import PrimazaCluster
from primazactl.primazamain.maincluster import MainCluster
from primazactl.cmd.worker.create.constants import APPLICATION
from .workercluster import WorkerCluster


class WorkerNamespace(PrimazaCluster):

    main_cluster: str = None
    type: str = None
    kube_namespace: Namespace = None
    cluster_environment: str = None
    role_config: str = None
    main: MainCluster = None
    worker: WorkerCluster = None
    secret_name: str = None
    secret_cfg: str = None

    def __init__(self, type,
                 namespace,
                 cluster_environment,
                 worker_cluster,
                 role_config,
                 main,
                 worker):

        super().__init__(namespace,
                         worker_cluster,
                         f"primaza-{self.type}-agent",
                         None)

        self.type = type
        self.main = main
        self.worker = worker
        api_client = self.kubeconfig.get_api_client()
        self.cluster_environment = cluster_environment
        self.kube_namespace = Namespace(api_client, namespace)
        self.role_config = role_config
        self.secret_name = "primaza-kubeconfig"

    def create(self):
        logger.log_entry(f"namespace type: {self.type}, "
                         f"cluster environment: {self.cluster_environment}, "
                         f"worker cluster: {self.worker.cluster_name}")

        # On worker cluster
        # - create the namespace
        self.kube_namespace.create()

        # Request a new service account from primaza main
        main_identity = self.main.create_primaza_identity(
            self.cluster_environment,
            self.type)

        # Get kubeconfig with secret from service accounf
        kc = self.main.get_kubeconfig(main_identity, self.cluster_name)

        # - in the created namespace, create the Secret
        #     'primaza-auth-$CLUSTER_ENVIRONMENT' the Worker key
        #     and the kubeconfig for authenticating with the Primaza cluster.
        self.create_namespaced_secret(self.secret_name, kc)

        # - In the created namespace, create the Role for the
        #   agent (named for example primaza-application-agent or
        #   primaza-service-agent), that will grant it access to
        #   namespace and its resources
        # - In the created namespace, create a RoleBinding for binding
        #   the agents' Service Account to the role defined above
        self.install_roles()

        # - In the created namespace, create a Role (named
        #   primaza-application or primaza-service), that will grant
        #   primaza access to namespace and its resources
        #   (e.g. create ServiceClaim, create RegisteredServices)
        api_client = self.kubeconfig.get_api_client()
        role_subscript = f"{self.worker.user}-{self.type}"
        primaza_policy = get_primaza_namespace_role(f"{role_subscript}-role",
                                                    self.namespace)
        primaza_role = Role(api_client, primaza_policy.metadata.name,
                            self.namespace, primaza_policy)
        primaza_role.create()

        # - In the created namespace, RoleBinding for binding the user primaza
        #   to the role defined above
        primaza_binding = RoleBinding(api_client,
                                      f"{role_subscript}-binding",
                                      self.namespace,
                                      primaza_role.name,
                                      self.worker.namespace,
                                      self.worker.user)
        primaza_binding.create()

        ce = self.main.get_cluster_environment()
        ce.add_namespace(self.type, self.namespace)
        logger.log_info(f"ce:{ce.body}")

    def check(self):
        logger.log_entry()

        error_messages = []
        if self.type == APPLICATION:
            error_message = self.check_service_account_roles(
                "primaza-controller-agentapp",
                "agentapp-role", self.namespace)
            if error_message:
                error_messages.extend(error_message)
        else:
            error_message = self.check_service_account_roles(
                "primaza-controller-agentsvc",
                "leader-election-role", self.namespace)
            if error_message:
                error_messages.extend(error_message)

            error_message = self.check_service_account_roles(
                "primaza-controller-agentsvc",
                "manager-role", self.namespace)
            if error_message:
                error_messages.extend(error_message)

        role = f"{self.worker.user}-{self.type}-role"
        error_message = self.worker.check_worker_roles(role, self.namespace)
        if error_message:
            error_messages.extend(error_message)

        if error_messages:
            raise RuntimeError(
                "Error: namespace install has failed to created the correct "
                f"accesses. Error messages were: {error_messages}")

        ce = self.main.get_cluster_environment()
        ce.check("Online", "Online", "True")

        logger.log_exit("All checks passed")

    def install_roles(self):
        logger.log_entry(f"config: {self.role_config}")
        out, err = self.kubectl_do(f"apply -f {self.role_config}")
        if err == 0:
            logger.log_entry("Deploy namespace config completed")

        logger.log_info(out)
        if err != 0:
            raise RuntimeError(
                "error deploying namespace config into "
                f"cluster {self.cluster_name}")
