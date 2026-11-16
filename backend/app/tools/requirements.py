from typing import Any

from app.tools.gateway import Gateway
from app.workflows import WORKFLOWS


def register(gateway: Gateway) -> None:
    def required_documents(claim_id: str) -> dict[str, Any]:
        """Each entry: any one of `any_of` satisfies it. Table: the workflow.
        `present` and `missing` are computed here: the model must not work out
        what is missing (issue #78)."""
        claim = gateway.store.get_claim(claim_id)
        if claim is None:
            raise KeyError(f"unknown claim: {claim_id}")
        workflow = WORKFLOWS.get(claim.type or "")
        if workflow is None:
            return {
                "documents": [],
                "missing": [],
                "note": "no table for this claim type yet",
            }
        have = {d.document_type for d in claim.documents}
        documents = [
            {**r, "present": bool(have & set(r["any_of"]))}
            for r in workflow.required_documents(claim.cause)
        ]
        missing = [d["label"] for d in documents if not d["present"]]
        return {"documents": documents, "missing": missing}

    gateway.register(
        "required_documents",
        required_documents,
        description=(
            "The documents this claim needs. Each entry has a `label`; any one of its "
            "`any_of` document types satisfies it. `present` says if the customer "
            "already uploaded it. `missing` is the list of the labels still to ask "
            "for: use it as it is. When `missing` is empty, nothing is missing."
        ),
    )
