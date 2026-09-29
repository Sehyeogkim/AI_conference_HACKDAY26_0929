"""Shared base class for every contract model."""

from pydantic import BaseModel, ConfigDict

CONTRACTS_VERSION = 2


class ContractModel(BaseModel):
    """Strict by default: unknown fields are rejected so format drift fails loudly.

    Models are immutable. In the exported (serialization) JSON Schema every field is required,
    because Python always writes every field; readers can rely on it being present.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_serialization_defaults_required=True)
