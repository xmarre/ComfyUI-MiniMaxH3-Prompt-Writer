export const CONTINUUM_SCHEMA_VERSION = 1;
export const CONTINUUM_MIN_CHUNKS = 1;
export const CONTINUUM_MAX_CHUNKS = 16;
export const CONTINUUM_MIN_SECONDS = 4;
export const CONTINUUM_MAX_SECONDS = 15;
export const CONTINUUM_SAMPLER_NODE_IDS = new Set(["H3ContinuumSamplerV34"]);

const CHUNK_HEADER = /^\s*\[\s*Chunk\s+(\d+)\s*\]\s*$/i;
const EDITABLE_MULTILINE_NODE_IDS = new Set(["PrimitiveStringMultiline"]);

export function normalizeContinuumSettings(value = {}) {
  const chunks = Number(value.chunks);
  const chunkSeconds = Number(value.chunk_seconds ?? value.chunkSeconds);
  if (!Number.isInteger(chunks) || chunks < CONTINUUM_MIN_CHUNKS || chunks > CONTINUUM_MAX_CHUNKS) {
    throw new Error(`Continuum chunks must be between ${CONTINUUM_MIN_CHUNKS} and ${CONTINUUM_MAX_CHUNKS}.`);
  }
  if (!Number.isFinite(chunkSeconds) || chunkSeconds < CONTINUUM_MIN_SECONDS || chunkSeconds > CONTINUUM_MAX_SECONDS) {
    throw new Error(`Continuum chunk duration must be between ${CONTINUUM_MIN_SECONDS} and ${CONTINUUM_MAX_SECONDS} seconds.`);
  }
  return {
    schema_version: CONTINUUM_SCHEMA_VERSION,
    chunks,
    chunk_seconds: chunkSeconds,
    total_seconds: Number((chunks * chunkSeconds).toFixed(6)),
  };
}

export function serializeContinuumPrompts(prompts) {
  if (!Array.isArray(prompts) || prompts.length === 0) throw new Error("A Continuum sequence needs at least one chunk.");
  return prompts.map((prompt, offset) => {
    const value = typeof prompt === "string" ? prompt.trim() : "";
    if (!value) throw new Error(`Chunk ${offset + 1} prompt is empty.`);
    if (value.split(/\r?\n/).some((line) => CHUNK_HEADER.test(line))) {
      throw new Error(`Chunk ${offset + 1} contains a reserved [Chunk N] header.`);
    }
    return `[Chunk ${offset + 1}]\n${value}`;
  }).join("\n\n");
}

export function parseContinuumPrompts(script, expectedChunks = null) {
  if (typeof script !== "string" || !script.trim()) throw new Error("Continuum sequence text is empty.");
  const prompts = [];
  let currentIndex = null;
  let body = [];
  const finish = () => {
    if (currentIndex == null) {
      if (body.some((line) => line.trim())) throw new Error("Text before [Chunk 1] is not canonical Continuum syntax.");
      body = [];
      return;
    }
    const prompt = body.join("\n").trim();
    if (!prompt) throw new Error(`Chunk ${currentIndex} prompt is empty.`);
    prompts.push(prompt);
    body = [];
  };
  script.split(/\r?\n/).forEach((line, offset) => {
    const match = line.match(CHUNK_HEADER);
    if (!match) {
      body.push(line);
      return;
    }
    finish();
    const index = Number(match[1]);
    const expected = prompts.length + 1;
    if (index !== expected) throw new Error(`Line ${offset + 1}: expected [Chunk ${expected}], found [Chunk ${index}].`);
    currentIndex = index;
  });
  finish();
  if (!prompts.length) throw new Error("No [Chunk N] sections were found.");
  if (expectedChunks != null && prompts.length !== expectedChunks) {
    throw new Error(`Expected ${expectedChunks} chunks, found ${prompts.length}.`);
  }
  return prompts;
}

export function sequenceStateFromResult(result) {
  const sequence = result?.sequence;
  const settings = normalizeContinuumSettings(sequence?.settings || {});
  if (sequence?.schema_version !== CONTINUUM_SCHEMA_VERSION || !sequence?.plan || !Array.isArray(sequence?.chunks)) {
    throw new Error("The generated Continuum response has no valid structural sequence state.");
  }
  const prompts = sequence.chunks.map((chunk, offset) => {
    if (chunk?.index !== offset + 1 || typeof chunk?.prompt !== "string" || !chunk.prompt.trim()) {
      throw new Error(`Generated Continuum Chunk ${offset + 1} is invalid.`);
    }
    return chunk.prompt.trim();
  });
  if (prompts.length !== settings.chunks) throw new Error("Generated Continuum chunk count does not match its settings.");
  const prompt = serializeContinuumPrompts(prompts);
  if (prompt !== result.prompt) throw new Error("Generated Continuum canonical text does not match its structural state.");
  return {
    schema_version: CONTINUUM_SCHEMA_VERSION,
    settings,
    plan: sequence.plan,
    prompts,
  };
}

export function continuumDraftOutput(value) {
  if (value?.raw_prompt != null) return String(value.raw_prompt);
  return serializeContinuumPrompts(value?.prompts || []);
}

export function updateContinuumDraftFromEditor(value, script) {
  const settings = normalizeContinuumSettings(value?.settings || {});
  try {
    const prompts = parseContinuumPrompts(script, settings.chunks);
    return { ...value, schema_version: CONTINUUM_SCHEMA_VERSION, settings, prompts, raw_prompt: null };
  } catch {
    return { ...value, schema_version: CONTINUUM_SCHEMA_VERSION, settings, raw_prompt: String(script) };
  }
}

function nodeClassId(node) {
  return node?.comfyClass || node?.type || node?.properties?.["Node name for S&R"] || "";
}

function inputNames(node) {
  return new Set((node?.inputs || []).map((input) => input?.name));
}

function widget(node, name) {
  return (node?.widgets || []).find((candidate) => candidate?.name === name) || null;
}

export function isCompatibleContinuumSampler(node) {
  if (!CONTINUUM_SAMPLER_NODE_IDS.has(nodeClassId(node))) return false;
  const inputs = inputNames(node);
  return inputs.has("sequence_prompt") && widget(node, "chunks") != null && widget(node, "chunk_seconds") != null;
}

export function compatibleContinuumSamplers(graph) {
  return (graph?._nodes || []).filter(isCompatibleContinuumSampler);
}

function selectedNodes(canvas) {
  const selected = canvas?.selected_nodes ?? canvas?.selectedItems;
  if (selected instanceof Map) return [...selected.values()];
  if (Array.isArray(selected)) return selected;
  if (selected && typeof selected === "object") return Object.values(selected);
  return [];
}

export function chooseContinuumSampler(app) {
  const candidates = compatibleContinuumSamplers(app?.graph);
  if (!candidates.length) return { status: "missing", candidates };
  if (candidates.length === 1) return { status: "selected", sampler: candidates[0], candidates };
  const selected = selectedNodes(app?.canvas).filter((node) => candidates.includes(node));
  if (selected.length === 1) return { status: "selected", sampler: selected[0], candidates };
  return { status: "multiple", candidates };
}

function graphLink(graph, linkId) {
  if (linkId == null) return null;
  if (graph?.links instanceof Map) return graph.links.get(linkId) || null;
  return graph?.links?.[linkId] || null;
}

function graphNode(graph, nodeId) {
  return graph?.getNodeById?.(nodeId) || (graph?._nodes || []).find((node) => node?.id === nodeId) || null;
}

function editableMultilineWidget(node) {
  if (!EDITABLE_MULTILINE_NODE_IDS.has(nodeClassId(node))) return null;
  const valueWidget = widget(node, "value") || (node?.widgets || []).find((candidate) => typeof candidate?.value === "string");
  const stringOutput = (node?.outputs || []).some((output) => output?.type === "STRING");
  return stringOutput && valueWidget ? valueWidget : null;
}

export function connectedSequenceTextSource(graph, sampler) {
  const input = (sampler?.inputs || []).find((candidate) => candidate?.name === "sequence_prompt");
  if (!input || input.link == null) return { status: "unconnected" };
  let link = graphLink(graph, input.link);
  const visited = new Set();
  while (link) {
    const source = graphNode(graph, link.origin_id);
    if (!source || visited.has(source.id)) break;
    visited.add(source.id);
    const sourceWidget = editableMultilineWidget(source);
    if (sourceWidget) return { status: "connected", node: source, widget: sourceWidget };
    const stringInputs = (source.inputs || []).filter((candidate) => candidate?.type === "STRING" && candidate.link != null);
    const stringOutputs = (source.outputs || []).filter((candidate) => candidate?.type === "STRING");
    if (stringInputs.length !== 1 || stringOutputs.length !== 1) break;
    link = graphLink(graph, stringInputs[0].link);
  }
  return { status: "incompatible_source" };
}

function setWidgetValue(app, node, target, value) {
  const previous = target.value;
  target.value = value;
  if (typeof target.callback === "function") target.callback(value, app?.canvas, node);
  if (typeof node?.onWidgetChanged === "function") node.onWidgetChanged(target.name, value, previous, target);
  node?.graph?.setDirtyCanvas?.(true, true);
  app?.graph?.setDirtyCanvas?.(true, true);
  app?.canvas?.setDirty?.(true, true);
  app?.graph?.change?.();
}

export function continuumSamplerSettings(sampler) {
  return {
    chunks: Number(widget(sampler, "chunks")?.value),
    chunk_seconds: Number(widget(sampler, "chunk_seconds")?.value),
  };
}

export function applySequenceToContinuum(app, sampler, sequenceState, { syncSettings = false } = {}) {
  if (!isCompatibleContinuumSampler(sampler)) return { status: "incompatible_sampler" };
  const settings = normalizeContinuumSettings(sequenceState?.settings || {});
  const prompt = serializeContinuumPrompts(sequenceState?.prompts || []);
  parseContinuumPrompts(prompt, settings.chunks);
  const current = continuumSamplerSettings(sampler);
  const mismatches = [];
  if (current.chunks !== settings.chunks) mismatches.push({ field: "chunks", writer: settings.chunks, sampler: current.chunks });
  if (Math.abs(current.chunk_seconds - settings.chunk_seconds) > 1e-6) {
    mismatches.push({ field: "chunk_seconds", writer: settings.chunk_seconds, sampler: current.chunk_seconds });
  }
  if (mismatches.length && !syncSettings) return { status: "mismatch", mismatches, sampler };
  if (syncSettings) {
    setWidgetValue(app, sampler, widget(sampler, "chunks"), settings.chunks);
    setWidgetValue(app, sampler, widget(sampler, "chunk_seconds"), settings.chunk_seconds);
  }
  const source = connectedSequenceTextSource(app?.graph, sampler);
  if (source.status !== "connected") return { ...source, sampler, prompt };
  setWidgetValue(app, source.node, source.widget, prompt);
  return { status: "applied", sampler, source: source.node, prompt, settings };
}

export function continuumSamplerLabel(node) {
  const title = String(node?.title || "H3 Continuum Sampler V3.4");
  return node?.id == null ? title : `${title} · node ${node.id}`;
}
