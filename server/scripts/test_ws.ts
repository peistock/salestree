const ws = new WebSocket("ws://localhost:8001/ws/chat");

const userId = process.argv[2] ?? "test_user";
const query = process.argv[3] ?? "现在几点？";

const TIMEOUT_MS = 180_000;
const timeout = setTimeout(() => {
  console.log("\n[timeout]");
  ws.close();
}, TIMEOUT_MS);

ws.onopen = () => {
  console.log("[connected]");
  ws.send(JSON.stringify({ user_id: userId, message: query }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data as string);
  if (data.type === "token") {
    process.stdout.write(data.content);
  } else {
    console.log("\n[event]", data.type, data.event_type ?? "", data.message ?? data.reply ?? "");
  }
  if (data.type === "result" || data.type === "error") {
    ws.close();
  }
};

ws.onerror = (err) => console.error("[error]", err);
ws.onclose = () => {
  clearTimeout(timeout);
  console.log("\n[closed]");
};
