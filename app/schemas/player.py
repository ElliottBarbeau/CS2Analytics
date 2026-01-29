from pydantic import BaseModel, Field
from pydantic import ConfigDict


class PlayerCreate(BaseModel):
    handle: str = Field(min_length=1, max_length=64)


class PlayerRead(BaseModel):
    id: int
    handle: str

    model_config = ConfigDict(from_attributes=True)
