import uuid
from datetime import datetime
from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    title: str | None = None
   


class SessionResponse(BaseModel):
    id: uuid.UUID
    title: str | None
    created_by: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True
        
class ParticipantResponse(BaseModel):
    user_id: uuid.UUID
    display_name: str

    class Config:
        from_attributes = True