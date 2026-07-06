/* QuantChive 智能助手聊天面板。自然语言问 → /api/agent/ask → 渲染 ReAct 轨迹 + 答案。
   ReAct 轨迹透明展示(思考/工具/观察),便于和 Hermes 等对比。红涨绿跌沿用。 */

const AGENT_HISTORY = [];   // 会话历史(前端维护,传给后端)

function renderAgentPanel() {
  const v = document.getElementById("view");
  v.innerHTML = "";
  const wrap = el("div", "agent-wrap");
  wrap.appendChild(el("h2", null, "智能助手 · 问资金流/行情"));
  const chat = el("div", "agent-chat");
  chat.id = "agent-chat";
  wrap.appendChild(chat);
  // 输入区
  const bar = el("div", "agent-input");
  const ta = el("textarea", "agent-ta");
  ta.placeholder = "例如：今天全市场主力净流入前5的股票？";
  ta.rows = 1;
  const send = el("button", "agent-send", "发送");
  bar.appendChild(ta); bar.appendChild(send);
  wrap.appendChild(bar);
  v.appendChild(wrap);

  // 示例问题(引导)
  if (!AGENT_HISTORY.length) {
    const tips = el("div", "agent-tips");
    ["今天哪个行业主力流入最多？", "全市场主力净流入前5的股票",
     "大盘今天主力净额多少？"].forEach((q) => {
      const b = el("button", "agent-tip", q);
      b.onclick = () => { ta.value = q; submit(); };
      tips.appendChild(b);
    });
    chat.appendChild(tips);
  }

  const submit = async () => {
    const q = ta.value.trim();
    if (!q) return;
    ta.value = "";
    const tips = chat.querySelector(".agent-tips");
    if (tips) tips.remove();
    _appendMsg(chat, "user", q);
    const thinking = el("div", "agent-msg assistant thinking", "🤔 思考中…");
    chat.appendChild(thinking); chat.scrollTop = chat.scrollHeight;
    try {
      const resp = await fetch("/api/agent/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, history: AGENT_HISTORY }),
      });
      const data = await resp.json();
      thinking.remove();
      _appendReactTrace(chat, data.steps || []);
      _appendMsg(chat, "assistant", data.answer || "(无回答)");
      if (data.provider) {
        chat.lastChild.appendChild(el("div", "agent-meta", `${data.provider} · ${data.model || ""}`));
      }
      // 维护历史(只存问答文本,轨迹不入历史避免膨胀)
      AGENT_HISTORY.push({ role: "user", content: q });
      AGENT_HISTORY.push({ role: "assistant", content: data.answer || "" });
    } catch (e) {
      thinking.remove();
      _appendMsg(chat, "assistant", "⚠️ 请求失败：" + (e.message || e));
    }
    chat.scrollTop = chat.scrollHeight;
  };
  send.onclick = submit;
  ta.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } };
  ta.focus();
}

function _appendMsg(chat, role, text) {
  const m = el("div", `agent-msg ${role}`);
  m.appendChild(el("div", "agent-bubble", _escape(text)));
  chat.appendChild(m);
}

// ReAct 轨迹：思考(灰) / 工具(蓝) / 观察(折叠)
function _appendReactTrace(chat, steps) {
  if (!steps.length) return;
  const box = el("div", "agent-trace");
  const head = el("summary", null, `🔍 推理轨迹（${steps.filter((s) => s.type === "tool").length} 次工具调用）`);
  const det = el("details", "agent-trace-det");
  det.appendChild(head);
  steps.forEach((s) => {
    if (s.type === "thought") {
      det.appendChild(el("div", "trace-thought", "💭 " + _escape(s.text)));
    } else if (s.type === "tool") {
      const args = Object.entries(s.arguments || {}).map(([k, v]) => `${k}=${v}`).join(", ");
      det.appendChild(el("div", "trace-tool", `🔧 ${s.name}(${_escape(args)})`));
    } else if (s.type === "observation") {
      det.appendChild(el("div", "trace-obs", "👁 " + _escape((s.summary || "").slice(0, 200))));
    }
  });
  box.appendChild(det);
  chat.appendChild(box);
}

function _escape(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}
