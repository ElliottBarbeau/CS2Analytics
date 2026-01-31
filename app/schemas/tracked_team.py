from pydantic import BaseModel, ConfigDict


class TrackedTeamRead(BaseModel):
    id: int
    name: str
    grid_team_id: str | None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class TrackedTeamUpdate(BaseModel):
    grid_team_id: str | None = None
    is_active: bool | None = None
