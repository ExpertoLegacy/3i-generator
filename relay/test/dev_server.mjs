// Runs the real relay locally with a simulated model, to try the app without a Cloudflare account.
//   node relay/test/dev_server.mjs 8787
// Set RRG_RELAY_URL=http://127.0.0.1:8787 before starting the app.
import { createServer } from "node:http";
import worker from "../src/index.js";
import { makeEnv } from "./harness.mjs";

// A 2x2 gray PNG, enough to exercise the image path.
const PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR4nGNoaGj4//8/AAkEAwFN5zHnAAAAAElFTkSuQmCC";

function simulated(_model, input) {
  if (input.prompt) return { image: PIXEL };
  const { messages } = input;
  const system = messages[0].content;
  let content;
  if (system.includes("TASK: Propose")) {
    content = { nodes: [{ id: "a", label: "Teacher", type: "Actor" }, { id: "b", label: "Borrow equipment", type: "Use case" }], edges: [{ from: "a", to: "b", label: "" }], missing_info: [] };
  } else if (system.includes("TASK: Suggest")) {
    content = { suggestions: ["Simulated option one for this field", "Simulated option two for this field", "Simulated option three for this field"] };
  } else if (system.includes("TASK: Review")) {
    content = { findings: [{ severity: "fix", message: "Simulated finding: state how often the current process happens." }, { severity: "consider", message: "Simulated finding: the second paragraph could be shorter." }] };
  } else {
    content = {
      blocks: [
        { type: "paragraph", text: "This simulated paragraph shows where the AI draft appears. The researchers reviewed the current process at the locale." },
        { type: "paragraph", text: "A second simulated paragraph claims 73 percent of records were lost (Invented, 2019), so the checks have something to flag." },
      ],
      citations_used: ["Invented, 2019"],
      missing_info: ["Simulated: how many transactions happen each week."],
    };
  }
  return { choices: [{ message: { content: JSON.stringify(content) } }], usage: { prompt_tokens: 2100, completion_tokens: 800 } };
}

const env = makeEnv(simulated);
const port = Number(process.argv[2] || 8787);
createServer(async (req, res) => {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const body = chunks.length ? Buffer.concat(chunks) : undefined;
  const response = await worker.fetch(new Request(`http://127.0.0.1:${port}${req.url}`, {
    method: req.method, headers: { authorization: req.headers.authorization || "", "cf-connecting-ip": "127.0.0.1" }, body,
  }), env);
  res.writeHead(response.status, { "content-type": "application/json" });
  res.end(await response.text());
}).listen(port, "127.0.0.1", () => console.log(`Simulated relay on ${port}`));
