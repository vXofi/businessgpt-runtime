from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


MAX_MESSAGE_CHARACTERS = 65_536
MAX_REQUEST_TEXT_CHARACTERS = 262_144
MAX_IMAGE_DATA_URL_CHARACTERS = 14_500_000
MAX_MESSAGES = 64
MAX_CONTENT_PARTS_PER_MESSAGE = 8
MAX_CONTENT_PARTS_PER_REQUEST = 128


class TextContentPart(BaseModel):
    type: Literal["text"]
    text: str = Field(..., min_length=1, max_length=MAX_MESSAGE_CHARACTERS)


class ImageURLValue(BaseModel):
    url: str = Field(..., min_length=1, max_length=MAX_IMAGE_DATA_URL_CHARACTERS)


class ImageURLContentPart(BaseModel):
    type: Literal["image_url"]
    image_url: ImageURLValue


ContentPart = Annotated[
    Union[TextContentPart, ImageURLContentPart],
    Field(discriminator="type"),
]
MessageText = Annotated[str, Field(min_length=1, max_length=MAX_MESSAGE_CHARACTERS)]
MessageParts = Annotated[
    list[ContentPart],
    Field(min_length=1, max_length=MAX_CONTENT_PARTS_PER_MESSAGE),
]


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: Union[MessageText, MessageParts]
    name: Optional[str] = Field(None, max_length=128)


class GenerateRequest(BaseModel):
    prompt: Optional[str] = Field(None, min_length=1, max_length=MAX_MESSAGE_CHARACTERS)
    messages: Optional[list[ChatMessage]] = Field(None, max_length=MAX_MESSAGES)
    system_prompt: Optional[str] = Field(
        None,
        min_length=1,
        max_length=MAX_MESSAGE_CHARACTERS,
    )
    max_tokens: int = Field(256, ge=1, le=1024)
    temperature: float = Field(0.7, ge=0.0, le=2.0, allow_inf_nan=False)
    top_p: float = Field(0.9, ge=0.0, le=1.0, allow_inf_nan=False)
    top_k: int = Field(50, ge=0, le=200)
    repetition_penalty: float = Field(1.1, ge=0.0, le=3.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_aggregate_limits(self) -> "GenerateRequest":
        text_characters = len(self.prompt or "") + len(self.system_prompt or "")
        content_parts = 0
        for message in self.messages or []:
            if isinstance(message.content, str):
                text_characters += len(message.content)
                continue
            content_parts += len(message.content)
            for part in message.content:
                if isinstance(part, TextContentPart):
                    text_characters += len(part.text)
        if text_characters > MAX_REQUEST_TEXT_CHARACTERS:
            raise ValueError(
                f"request text exceeds {MAX_REQUEST_TEXT_CHARACTERS} characters"
            )
        if content_parts > MAX_CONTENT_PARTS_PER_REQUEST:
            raise ValueError(
                f"request exceeds {MAX_CONTENT_PARTS_PER_REQUEST} content parts"
            )
        return self


class GenerateResponse(BaseModel):
    response: str
    model: str
    elapsed_ms: int
    usage: dict
