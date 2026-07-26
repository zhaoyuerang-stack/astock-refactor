"""Human Approval and Signature Workflows.

Implements structural workflows for Model Risk committees (Research Review,
Model Risk Validation Review, Investment Policy Committee).
"""
from __future__ import annotations

import time

from model_risk.model_inventory import ModelCard, ModelInventory


class ApprovalWorkflow:
    def __init__(self, inventory: ModelInventory):
        self.inventory = inventory

    def request_approval(self, strategy_id: str, owner: str) -> ModelCard | None:
        """Initiate approval workflow for a given strategy."""
        card = self.inventory.get_card(strategy_id)
        if not card:
            return None
        
        card.owner = owner
        card.approval_status = "PENDING"
        self.inventory.register_card(card)
        return card

    def approve_model(
        self,
        strategy_id: str,
        approver: str,
        notes: str = ""
    ) -> ModelCard | None:
        """Approve model and sign it off."""
        card = self.inventory.get_card(strategy_id)
        if not card:
            return None

        card.approver = approver
        card.approval_status = "APPROVED"
        card.signature = f"SIG_{approver.upper()}_{int(time.time())}"
        card.metadata["approval_notes"] = notes
        card.metadata["approved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        
        self.inventory.register_card(card)
        return card

    def reject_model(
        self,
        strategy_id: str,
        approver: str,
        notes: str = ""
    ) -> ModelCard | None:
        """Reject model and log reasons."""
        card = self.inventory.get_card(strategy_id)
        if not card:
            return None

        card.approver = approver
        card.approval_status = "REJECTED"
        card.metadata["rejection_notes"] = notes
        card.metadata["rejected_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        self.inventory.register_card(card)
        return card
