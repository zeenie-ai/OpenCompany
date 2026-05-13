from .._specialized import SpecializedAgentBase


class PaymentsAgentNode(SpecializedAgentBase):
    type = "payments_agent"
    display_name = "Payments Agent"
    subtitle = "Payment Processing"
    group = ("agent",)
    description = "AI agent for payment processing"
