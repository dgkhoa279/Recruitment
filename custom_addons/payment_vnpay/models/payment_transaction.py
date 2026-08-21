# -*- coding: utf-8 -*-
from odoo import models, fields, api


class PaymentTransactionVnpay(models.Model):
    _inherit = "payment.transaction"

    def _get_specific_processing_values(self, processing_values):
        """Override to add vnpay_checkout_url to processing values sent to JS client."""
        res = super()._get_specific_processing_values(processing_values)
        if self.provider_code != "vnpay":
            return res

        # Generate signed checkout URL and pass it directly to JS client
        checkout_url = self.provider_id.vnpay_generate_checkout_url(
            amount=self.amount,
            reference=self.reference,
            order_info=f"Thanh toan giao dich {self.reference}",
        )
        res['vnpay_checkout_url'] = checkout_url
        return res

    def _get_specific_rendering_values(self, processing_values):
        """Override to provide rendering values for QWeb redirect_form template (fallback)."""
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != "vnpay":
            return res

        checkout_url = self.provider_id.vnpay_generate_checkout_url(
            amount=self.amount,
            reference=self.reference,
            order_info=f"Thanh toan giao dich {self.reference}",
        )
        return {
            "api_url": checkout_url,
        }
