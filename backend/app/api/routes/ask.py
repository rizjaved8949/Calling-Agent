"""
A staff member checking something mid-call, without putting the caller on
hold.

Shares the same Gemini text model and the same "answer only from the
material" discipline as a WhatsApp reply, and the same knowledge-base
resolution every other answer in this product uses — see `services/routing.py`
and `services/agent/reply.ask`.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ...errors import AppError
from ...repositories import agents as agent_repo
from ...repositories import knowledge as knowledge_repo
from ...services.agent import reply
from ..deps import CurrentTenant

router = APIRouter(prefix="/ask", tags=["ask"])


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    knowledge_base_id: str = Field(default="", alias="knowledgeBaseId")

    model_config = {"populate_by_name": True}


@router.post("")
async def ask(tenant: CurrentTenant, payload: AskRequest) -> dict:
    if not reply.available():
        raise AppError(
            503, "Looking things up is not configured on this deployment.",
            code="not_configured",
        )
    if payload.knowledge_base_id:
        kb = await agent_repo.get_knowledge_base(tenant.phone_number_id, payload.knowledge_base_id)
        if kb is None:
            raise AppError(422, "That knowledge base does not exist.", code="no_such_kb")

    context = await knowledge_repo.context_for(
        tenant.phone_number_id, knowledge_base_id=payload.knowledge_base_id
    )
    return await reply.ask(tenant, payload.question, context)
