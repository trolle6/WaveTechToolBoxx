"""Secret Santa slash commands — combines domain mixins into one cog interface."""
from .secret_santa_cmd_admin import SecretSantaAdminMixin
from .secret_santa_cmd_lifecycle import SecretSantaLifecycleMixin
from .secret_santa_cmd_participant import SecretSantaParticipantMixin
from .secret_santa_cmd_root import SecretSantaRootMixin


class SecretSantaCommandsMixin(
    SecretSantaAdminMixin,
    SecretSantaParticipantMixin,
    SecretSantaLifecycleMixin,
    SecretSantaRootMixin,
):
    """All /ss commands and SS event listeners."""
