from typing import Optional

from pydantic import ConfigDict, Field

from .._base import AndroidServiceBase, AndroidServiceParams


class AppLauncherParams(AndroidServiceParams):
    """Extends the shared Android Params with a conditional package_name
    field, required when action='launch'.
    """

    package_name: Optional[str] = Field(
        default=None,
        description="Android application package name (e.g. com.whatsapp)",
        json_schema_extra={
            "displayOptions": {"show": {"action": ["launch"]}},
        },
    )

    model_config = ConfigDict(extra="allow")


class AppLauncherNode(AndroidServiceBase):
    type = "appLauncher"
    display_name = "App Launcher"
    description = "Launch applications by package name"

    Params = AppLauncherParams
