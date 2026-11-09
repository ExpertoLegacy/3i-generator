// The built-in AI relay. It holds no API key: Workers AI is reached through a binding.
// Prompt and response text is never logged or stored. D1 holds only anonymous counters.
import { BASE, DIAGRAM_KINDS, GUIDED_WRITE, EVIDENCE_WORDS, IMAGE_SLOTS, MODES, TENSE } from "../prompts/base.js";
import parts from "../prompts/parts.json" with { type: "json" };

const MAX_BODY_BYTES = 40000;
const MAX_HINT_CHARS = 400;
const MAX_IMAGE_PROMPT_CHARS = 400;
const IMAGE_STEPS = 4;
const IMAGE_ATTEMPTS = 2;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const encoder = new TextEncoder();

class Failure extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });

const today = () => new Date().toISOString().slice(0, 10);
const minute = () => new Date().toISOString().slice(0, 16);

async function hmac(secret, text) {
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(text));
  return [...new Uint8Array(signature)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function sameText(a, b) {
  if (a.length !== b.length) return false;
  let difference = 0;
  for (let i = 0; i < a.length; i += 1) difference |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return difference === 0;
}

// A token is the install ID signed with TOKEN_SECRET. Changing the secret invalidates every
// token, and the app registers again by itself, so rotation needs no reinstall.
async function installFrom(request, env) {
  const header = request.headers.get("authorization") || "";
  const [id, signature] = header.replace(/^Bearer\s+/i, "").split(".");
  if (id && signature && UUID.test(id) && sameText(signature, await hmac(env.TOKEN_SECRET, id))) return id;
  throw new Failure(401, "register_again", "This copy of the app needs to register again.");
}

async function ipScope(request, env) {
  const address = request.headers.get("cf-connecting-ip") || "unknown";
  return (await hmac(env.TOKEN_SECRET, address)).slice(0, 16);
}

function bump(env, day, scope, neurons, requests) {
  return env.DB.prepare(
    `INSERT INTO usage(day, scope, neurons, requests) VALUES(?, ?, ?, ?)
     ON CONFLICT(day, scope) DO UPDATE SET neurons = neurons + excluded.neurons, requests = requests + excluded.requests
     RETURNING neurons, requests`,
  ).bind(day, scope, neurons, requests);
}

async function limitIp(request, env) {
  const scope = `ip:${await ipScope(request, env)}:${minute()}`;
  const row = (await bump(env, today(), scope, 0, 1).run()).results[0];
  if (row.requests > Number(env.IP_REQUESTS_PER_MINUTE)) {
    throw new Failure(429, "too_fast", "Too many AI requests from this network. Wait a minute and try again.");
  }
}

function limits(env) {
  return { install: Number(env.INSTALL_DAILY_NEURONS), overall: Number(env.OVERALL_DAILY_NEURONS) };
}

async function quota(env, install) {
  const day = today();
  const rows = await env.DB.prepare("SELECT scope, neurons FROM usage WHERE day = ? AND scope IN ('all', ?)").bind(day, `i:${install}`).all();
  const used = Object.fromEntries(rows.results.map((row) => [row.scope, row.neurons]));
  const max = limits(env);
  return {
    install_remaining: Math.max(0, max.install - (used[`i:${install}`] || 0)),
    overall_remaining: Math.max(0, max.overall - (used.all || 0)),
  };
}

// Reserve the worst-case cost first so simultaneous requests cannot pass the daily limit.
async function reserve(env, install, neurons) {
  const day = today();
  const [all, own] = await env.DB.batch([bump(env, day, "all", neurons, 1), bump(env, day, `i:${install}`, neurons, 1)]);
  const max = limits(env);
  const overallOver = all.results[0].neurons > max.overall;
  const installOver = own.results[0].neurons > max.install;
  if (overallOver || installOver) {
    await settle(env, install, -neurons, -1);
    if (overallOver) throw new Failure(429, "pool_used_up", "Today's shared AI allowance is used up. It resets at 8:00 AM Philippine time.");
    throw new Failure(429, "install_used_up", "This computer has used its AI allowance for today. It resets at 8:00 AM Philippine time.");
  }
}

function settle(env, install, neurons, requests) {
  const day = today();
  return env.DB.batch([bump(env, day, "all", neurons, requests), bump(env, day, `i:${install}`, neurons, requests)]);
}

function cost(env, inputTokens, outputTokens) {
  return Math.ceil((inputTokens * Number(env.NEURONS_PER_MILLION_INPUT) + outputTokens * Number(env.NEURONS_PER_MILLION_OUTPUT)) / 1e6);
}

async function readBody(request) {
  const text = await request.text();
  if (encoder.encode(text).length > MAX_BODY_BYTES) throw new Failure(413, "too_long", "This part is too long for one AI request. Shorten your answers and try again.");
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    throw new Failure(400, "bad_request", "The request could not be read.");
  }
  if (typeof body !== "object" || body === null || Array.isArray(body)) throw new Failure(400, "bad_request", "The request could not be read.");
  return body;
}

export function buildMessages(body) {
  const part = Object.hasOwn(parts.parts, String(body.part_key)) ? parts.parts[body.part_key] : null;
  if (!part || !part.modes.includes(body.mode)) throw new Failure(400, "not_allowed", "AI is not available for this part.");
  if (typeof body.masked_input !== "object" || body.masked_input === null || Array.isArray(body.masked_input)) throw new Failure(400, "bad_request", "The request could not be read.");
  const tense = part.tense === "stage" ? (body.stage === "proposal" ? "future" : "past") : part.tense;
  const sections = [BASE, TENSE[tense] || TENSE.free, MODES[body.mode].replace("{QUESTIONS}", part.guided_questions), `PART: ${part.label}`];
  if (body.mode === "write" && part.modes.includes("guided")) sections.push(GUIDED_WRITE);
  // The template's guidelines are advice about writing, which only confuses a diagram request.
  if (part.guideline && body.mode !== "diagram") sections.push(`GUIDELINE:\n${part.guideline}`);
  if (body.mode === "options") {
    sections.push(`OPTION RULE:\n${part.options_rule}`);
  } else if (body.mode === "topics") {
    sections.push(`TOPIC RULE:\n${part.topics_rule}`);
  } else if (body.mode === "diagram") {
    if (!part.diagram_kinds.includes(body.masked_input.kind) || !Object.hasOwn(DIAGRAM_KINDS, body.masked_input.kind)) throw new Failure(400, "not_allowed", "AI is not available for this diagram.");
    sections.push(`KIND:\n${DIAGRAM_KINDS[body.masked_input.kind]}`);
  } else if (part.rules) sections.push(`PART RULES:\n${part.rules}`);
  if (body.mode === "suggest") {
    const field = part.suggest[body.masked_input.field];
    if (!field) throw new Failure(400, "not_allowed", "AI is not available for this field.");
    sections.push(`FIELD:\n${field}`);
  }
  let user = `INPUT:\n${JSON.stringify(body.masked_input)}`;
  if (typeof body.fix_hint === "string" && body.fix_hint) {
    user += `\n\nYour previous reply could not be used: ${body.fix_hint.slice(0, MAX_HINT_CHARS)}\nReturn corrected JSON only.`;
  }
  return { messages: [{ role: "system", content: sections.join("\n\n") }, { role: "user", content: user }], maxOutput: part.max_output };
}

function outputOf(result) {
  if (typeof result?.response === "string") return result.response;
  const content = result?.choices?.[0]?.message?.content;
  return typeof content === "string" ? content : "";
}

async function text(request, env) {
  const install = await installFrom(request, env);
  await limitIp(request, env);
  const body = await readBody(request);
  const { messages, maxOutput } = buildMessages(body);
  const inputChars = messages.reduce((sum, m) => sum + m.content.length, 0);
  const reserved = cost(env, Math.ceil(inputChars / 3), maxOutput);
  await reserve(env, install, reserved);

  let result;
  try {
    // Hidden reasoning used up the output limit and returned empty drafts when it was left on.
    result = await env.AI.run(env.TEXT_MODEL, { messages, max_completion_tokens: maxOutput, temperature: 0.4, chat_template_kwargs: { enable_thinking: false } });
  } catch (error) {
    await settle(env, install, -reserved, -1);
    if (/neuron|daily free allocation|4006/i.test(String(error?.message))) throw new Failure(429, "pool_used_up", "Today's shared AI allowance is used up. It resets at 8:00 AM Philippine time.");
    throw new Failure(502, "model_failed", "The AI service did not respond. Try again in a moment.");
  }

  const output = outputOf(result);
  const inputTokens = result?.usage?.prompt_tokens ?? Math.ceil(inputChars / 4);
  const outputTokens = result?.usage?.completion_tokens ?? Math.ceil(output.length / 4);
  const neurons = cost(env, inputTokens, outputTokens);
  await settle(env, install, neurons - reserved, 0);
  if (!output) throw new Failure(502, "model_failed", "The AI service returned nothing. Try again in a moment.");
  return json({
    ok: true, text: output, model: env.TEXT_MODEL, prompt_version: parts.version,
    usage: { input_tokens: inputTokens, output_tokens: outputTokens, neurons },
    quota: await quota(env, install),
  });
}

async function image(request, env) {
  const install = await installFrom(request, env);
  await limitIp(request, env);
  const body = await readBody(request);
  const slot = Object.hasOwn(IMAGE_SLOTS, String(body.slot)) ? IMAGE_SLOTS[body.slot] : null;
  if (!slot) throw new Failure(400, "not_allowed", "AI images are not available for this item.");
  const description = typeof body.prompt === "string" ? body.prompt.trim() : "";
  if (!description || description.length > MAX_IMAGE_PROMPT_CHARS) throw new Failure(400, "bad_request", "Describe the image in one or two short sentences.");
  if (EVIDENCE_WORDS.test(description)) throw new Failure(400, "evidence", "AI images cannot be used for screenshots, photos, results, letters, certificates, or signatures. Use your real files for those.");
  const neurons = Number(env.IMAGE_NEURONS);
  await reserve(env, install, neurons);
  const input = { prompt: `${slot.before} ${description}. ${slot.after}`, steps: IMAGE_STEPS };
  let result;
  for (let attempt = 0; attempt < IMAGE_ATTEMPTS && !result?.image; attempt += 1) {  // the model fails now and then for no lasting reason
    try {
      result = await env.AI.run(env.IMAGE_MODEL, input);
    } catch (error) {
      if (/neuron|daily free allocation|4006/i.test(String(error?.message))) {
        await settle(env, install, -neurons, -1);
        throw new Failure(429, "pool_used_up", "Today's shared AI allowance is used up. It resets at 8:00 AM Philippine time.");
      }
    }
  }
  if (typeof result?.image !== "string" || !result.image) {
    await settle(env, install, -neurons, -1);
    throw new Failure(502, "model_failed", "The image could not be created. Try again, or describe it differently.");
  }
  return json({ ok: true, image_base64: result.image, model: env.IMAGE_MODEL, prompt_version: parts.version, usage: { neurons }, quota: await quota(env, install) });
}

async function register(request, env) {
  const scope = `reg:${await ipScope(request, env)}`;
  const row = (await bump(env, today(), scope, 0, 1).run()).results[0];
  if (row.requests > Number(env.IP_REGISTRATIONS_PER_DAY)) throw new Failure(429, "too_fast", "Too many new installs from this network today. Try again tomorrow.");
  const body = await readBody(request);
  if (typeof body.install_id !== "string" || !UUID.test(body.install_id)) throw new Failure(400, "bad_request", "The request could not be read.");
  return json({ ok: true, install_token: `${body.install_id}.${await hmac(env.TOKEN_SECRET, body.install_id)}` });
}

async function route(request, env) {
  const { pathname } = new URL(request.url);
  const key = `${request.method} ${pathname}`;
  if (key === "GET /v1/health") return json({ ok: true, prompt_version: parts.version, latest_app_version: env.LATEST_APP_VERSION || "", download_url: env.DOWNLOAD_URL || "" });
  if (key === "POST /v1/register") return register(request, env);
  if (key === "GET /v1/quota") return json({ ok: true, quota: await quota(env, await installFrom(request, env)) });
  if (key === "POST /v1/text") return text(request, env);
  if (key === "POST /v1/image") return image(request, env);
  throw new Failure(404, "not_found", "Not found.");
}

export default {
  async fetch(request, env) {
    try {
      return await route(request, env);
    } catch (error) {
      if (error instanceof Failure) return json({ ok: false, error: error.code, message: error.message }, error.status);
      return json({ ok: false, error: "relay_failed", message: "The AI service is having a problem. Try again later." }, 503);
    }
  },
  async scheduled(_event, env) {
    const cutoff = new Date(Date.now() - 3 * 86400000).toISOString().slice(0, 10);
    await env.DB.prepare("DELETE FROM usage WHERE day < ?").bind(cutoff).run();
  },
};
