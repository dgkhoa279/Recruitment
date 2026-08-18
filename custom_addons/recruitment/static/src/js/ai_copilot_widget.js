/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class AICopilotWidget extends Component {
    static template = "recruitment.AICopilotWidget";

    setup() {
        this.rpc = useService("rpc");
        this.notification = useService("notification");
        this.action = useService("action");

        this.state = useState({
            isOpen: false,
            isLoading: false,
            inputMessage: "",
            messages: [
                {
                    sender: "ai",
                    text: "🤖 **Xin chào!** Tôi là Trợ lý AI Copilot Tuyển dụng. Bạn có thể hỏi tư vấn nhân sự hoặc yêu cầu tôi **tạo Tool tự động hóa Odoo** ngay tại đây!",
                }
            ],
        });
    }

    togglePanel() {
        this.state.isOpen = !this.state.isOpen;
    }

    async sendMessage() {
        const text = this.state.inputMessage.trim();
        if (!text || this.state.isLoading) return;

        this.state.messages.push({ sender: "user", text: text });
        this.state.inputMessage = "";
        this.state.isLoading = true;

        const historyList = this.state.messages.slice(-10).map(m => ({
            role: m.sender === 'user' ? 'user' : 'model',
            content: m.text,
        }));

        try {
            const res = await this.rpc("/recruitment/ai_copilot/chat", {
                message: text,
                history: historyList,
            });
            if (res.error) {
                this.state.messages.push({ sender: "ai", text: `⚠️ ${res.error}` });
            } else {
                this.state.messages.push({
                    sender: "ai",
                    text: res.ai_reply || "Đã xử lý xong!",
                    tool_id: res.tool_id,
                    tool_name: res.tool_name,
                });
            }
        } catch (e) {
            this.state.messages.push({ sender: "ai", text: `❌ Lỗi kết nối AI: ${e.message || e}` });
        } finally {
            this.state.isLoading = false;
        }
    }

    async executeTool(toolId, toolName) {
        if (!toolId) return;
        this.notification.add(`Đang kích hoạt Tool [${toolName}]...`, { type: "info" });
        try {
            const res = await this.rpc("/recruitment/ai_copilot/execute_tool", { tool_id: toolId });
            if (res.error) {
                this.notification.add(`Lỗi chạy Tool: ${res.error}`, { type: "danger" });
            } else {
                this.notification.add(`✅ Thực thi Tool [${toolName}] thành công!`, { type: "success" });
                if (res.result && typeof res.result === "object" && res.result.type) {
                    this.action.doAction(res.result);
                }
            }
        } catch (e) {
            this.notification.add(`Lỗi: ${e.message || e}`, { type: "danger" });
        }
    }

    onKeyDown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.sendMessage();
        }
    }
}

registry.category("main_components").add("AICopilotWidget", {
    Component: AICopilotWidget,
});
