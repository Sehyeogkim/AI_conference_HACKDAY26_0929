// Drives the WeFarm page in a separately launched Chrome over the DevTools protocol (port 9333),
// for quick checks from a terminal. Launch Chrome first, e.g. on macOS:
//   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --user-data-dir=/tmp/wefarm-chrome --remote-debugging-port=9333 http://127.0.0.1:5180/
// Usage: node web/tools/browserControl.mjs reload | eval "<js>" | shot <file.jpg> | key <Code> [holdMs] | click <x> <y>
const targets = await (await fetch("http://127.0.0.1:9333/json")).json();
const target = targets.find((t) => t.type === "page" && t.url.includes("127.0.0.1:5180"));
if (!target) { console.log("no WeFarm page"); process.exit(1); }
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve) => socket.addEventListener("open", resolve));
let nextId = 1;
const pending = new Map();
socket.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (message.id && pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); }
});
const send = (method, params = {}) => new Promise((resolve) => { const id = nextId++; pending.set(id, resolve); socket.send(JSON.stringify({ id, method, params })); });
const [action, ...args] = process.argv.slice(2);
const evaluate = async (expression) => (await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true })).result?.result?.value;
if (action === "reload") {
  // Capture console output into window.__log from the first line of the page.
  await send("Page.enable");
  await send("Page.addScriptToEvaluateOnNewDocument", { source: `window.__log=[];for(const k of ["log","warn","error","info"]){const o=console[k].bind(console);console[k]=(...a)=>{window.__log.push(k+": "+a.map(x=>x&&x.stack?x.stack:typeof x==="object"?JSON.stringify(x):String(x)).join(" "));o(...a)}}` });
  await send("Page.reload", { ignoreCache: true, ...(args[0] ? {} : {}) });
  if (args[0]) await send("Page.navigate", { url: args[0] });
  console.log("reloaded");
} else if (action === "eval") {
  console.log(JSON.stringify(await evaluate(args[0]), null, 1));
} else if (action === "shot") {
  const result = await send("Page.captureScreenshot", { format: "jpeg", quality: 70 });
  (await import("node:fs")).writeFileSync(args[0], Buffer.from(result.result.data, "base64"));
  console.log("saved", args[0]);
} else if (action === "key") {
  const code = args[0]; const hold = Number(args[1] ?? 0);
  const key = code.startsWith("Key") ? code.slice(3).toLowerCase() : code === "Space" ? " " : code === "BracketRight" ? "]" : code === "BracketLeft" ? "[" : code;
  await send("Input.dispatchKeyEvent", { type: "keyDown", code, key });
  if (hold) await new Promise((r) => setTimeout(r, hold));
  await send("Input.dispatchKeyEvent", { type: "keyUp", code, key });
  console.log("key", code, hold);
} else if (action === "click") {
  const [x, y] = args.map(Number);
  for (const type of ["mousePressed", "mouseReleased"]) await send("Input.dispatchMouseEvent", { type, x, y, button: "left", clickCount: 1 });
  console.log("clicked", x, y);
}
socket.close();
