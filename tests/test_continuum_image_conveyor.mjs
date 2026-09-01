import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const continuumSource = await readFile(new URL("../web/continuum.js", import.meta.url), "utf8");
const continuumEncoded = Buffer.from(continuumSource).toString("base64");
const {
  discoverContinuumReferenceInventory,
  sameContinuumReferenceInventory,
  validateContinuumModeTopology,
} = await import(`data:text/javascript;base64,${continuumEncoded}`);

const OUTPUT_NAMES = [
  "image",
  "mask",
  "path",
  "index",
  "remaining_pending",
  "source_path",
  "ref_image_1",
  "ref_image_2",
  "ref_image_3",
  "ref_image_4",
  "ref_image_5",
  "ref_image_6",
  "ref_image_7",
  "ref_image_8",
  "last_frame",
];

function samplerNode(id = 100) {
  return {
    id,
    type: "H3ContinuumSamplerV34",
    inputs: [
      { name: "sequence_prompt", type: "STRING", link: null },
      { name: "first_frame", type: "IMAGE", link: null },
      { name: "last_frame", type: "IMAGE", link: null },
      ...Array.from({ length: 8 }, (_, index) => ({
        name: `reference_image_${index + 1}`,
        type: "IMAGE",
        link: null,
      })),
      { name: "reference_video_1", type: "IMAGE", link: null },
      { name: "driving_audio", type: "AUDIO", link: null },
    ],
    outputs: [],
    widgets: [
      { name: "prompt_mode", value: "Timeline" },
      { name: "chunks", value: 3 },
      { name: "chunk_seconds", value: 5 },
    ],
  };
}

function conveyorNode({
  id = 10,
  type = "ImageConveyor",
  outputMode = "persistent_refs",
  imagesPerExecution = 1,
  referenceSlots = Array(8).fill(null),
  state = {},
  properties = {},
} = {}) {
  const stateJson = JSON.stringify({
    version: 2,
    items: [],
    dont_consume: false,
    images_per_execution: imagesPerExecution,
    output_mode: outputMode,
    reference_slots: referenceSlots,
    ...state,
  });
  return {
    id,
    type,
    inputs: [],
    outputs: OUTPUT_NAMES.map((name) => ({ name, type: name === "mask" ? "MASK" : name.startsWith("ref_image_") || name === "image" || name === "last_frame" ? "IMAGE" : "*", links: [] })),
    widgets: [{ name: "state_json", value: stateJson }],
    properties: { ...properties },
  };
}

function transformNode(id = 20) {
  return {
    id,
    type: "ImageScaleToTotalPixelsX",
    inputs: [{ name: "image", type: "IMAGE", link: null }],
    outputs: [{ name: "IMAGE", type: "IMAGE", links: [] }],
    widgets: [],
  };
}

function bypassNode(id = 30) {
  return {
    id,
    type: "Reroute",
    mode: 4,
    inputs: [{ name: "input", type: "IMAGE", link: null }],
    outputs: [{ name: "output", type: "IMAGE", links: [] }],
    widgets: [],
  };
}

function graphWith(nodes) {
  let nextLink = 1;
  const links = {};
  const graph = {
    _nodes: nodes,
    links,
    getNodeById(id) { return this._nodes.find((node) => node.id === id) || null; },
  };
  nodes.forEach((node) => { node.graph = graph; });
  return {
    graph,
    connect(source, sourceSlot, target, inputName) {
      const targetSlot = target.inputs.findIndex((input) => input.name === inputName);
      assert.notEqual(targetSlot, -1, `missing input ${inputName}`);
      const linkId = nextLink++;
      links[linkId] = {
        origin_id: source.id,
        origin_slot: sourceSlot,
        target_id: target.id,
        target_slot: targetSlot,
      };
      source.outputs[sourceSlot].links.push(linkId);
      target.inputs[targetSlot].link = linkId;
      return linkId;
    },
    app: { graph, canvas: { selected_nodes: {} } },
  };
}

const ref = (name) => ({
  annotated: `${name}.png [input]`,
  filename: `${name}.png`,
  subfolder: "",
  type: "input",
});

test("persistent Image Conveyor excludes disabled and empty shelf outputs and disabled Main through a transform", () => {
  const sampler = samplerNode();
  const conveyor = conveyorNode({
    referenceSlots: [ref("one"), null, ref("three"), ...Array(5).fill(null)],
    state: {
      reference_output_enabled: Array(8).fill(true),
      main_output_enabled: true,
      last_frame_output_enabled: true,
    },
    properties: {
      image_conveyor_reference_enabled: [true, true, false, true, true, true, true, true],
      image_conveyor_main_enabled: false,
    },
  });
  const transform = transformNode();
  const { app, connect } = graphWith([sampler, conveyor, transform]);

  connect(conveyor, 0, transform, "image");
  connect(transform, 0, sampler, "first_frame");
  connect(conveyor, 6, sampler, "reference_image_1");
  connect(conveyor, 7, sampler, "reference_image_2");
  connect(conveyor, 8, sampler, "reference_image_3");
  connect(conveyor, 14, sampler, "last_frame");

  const inventory = discoverContinuumReferenceInventory(app, sampler);
  assert.deepEqual(
    inventory.items.map((item) => [item.role, item.tag ?? null, item.input_name]),
    [
      ["reference_image", "<Picture 1>", "reference_image_1"],
      ["last_frame", null, "last_frame"],
    ],
  );
  assert.deepEqual(validateContinuumModeTopology("Reference", inventory).actual, {
    first_frame: false,
    last_frame: true,
    reference_images: 1,
  });
});

test("persistent Image Conveyor properties override stale serialized toggle snapshots", () => {
  const sampler = samplerNode();
  const conveyor = conveyorNode({
    referenceSlots: [ref("one"), ...Array(7).fill(null)],
    state: {
      reference_output_enabled: Array(8).fill(true),
      main_output_enabled: true,
      last_frame_output_enabled: true,
    },
    properties: {
      image_conveyor_reference_enabled: [false, true, true, true, true, true, true, true],
      image_conveyor_last_frame_enabled: false,
    },
  });
  const { app, connect } = graphWith([sampler, conveyor]);
  connect(conveyor, 6, sampler, "reference_image_1");
  connect(conveyor, 14, sampler, "last_frame");

  assert.deepEqual(discoverContinuumReferenceInventory(app, sampler).items, []);
});

test("queue-group Image Conveyor exposes only configured group members and ignores persistent toggles", () => {
  const sampler = samplerNode();
  const conveyor = conveyorNode({
    type: "SequentialBatchImageLoader",
    outputMode: "queue_group",
    imagesPerExecution: 3,
    state: {
      dont_consume: true,
      reference_output_enabled: Array(8).fill(false),
      main_output_enabled: false,
      last_frame_output_enabled: false,
    },
    properties: {
      image_conveyor_reference_enabled: Array(8).fill(false),
      image_conveyor_main_enabled: false,
      image_conveyor_last_frame_enabled: false,
    },
  });
  const { app, connect } = graphWith([sampler, conveyor]);

  connect(conveyor, 0, sampler, "first_frame");
  connect(conveyor, 6, sampler, "reference_image_1");
  connect(conveyor, 7, sampler, "reference_image_3");
  connect(conveyor, 8, sampler, "reference_image_4");
  connect(conveyor, 14, sampler, "last_frame");

  const inventory = discoverContinuumReferenceInventory(app, sampler);
  assert.deepEqual(
    inventory.items.map((item) => [item.role, item.tag ?? null, item.input_name]),
    [
      ["reference_image", "<Picture 1>", "reference_image_1"],
      ["reference_image", "<Picture 2>", "reference_image_3"],
      ["first_frame", null, "first_frame"],
      ["last_frame", null, "last_frame"],
    ],
  );
  assert.equal(inventory.items.some((item) => item.input_name === "reference_image_4"), false);
});

test("bypassed nodes resolve back to Image Conveyor effective reference state", () => {
  const sampler = samplerNode();
  const conveyor = conveyorNode({
    referenceSlots: [null, ...Array(7).fill(null)],
  });
  const bypass = bypassNode();
  const { app, connect } = graphWith([sampler, conveyor, bypass]);

  connect(conveyor, 6, bypass, "input");
  connect(bypass, 0, sampler, "reference_image_1");

  assert.deepEqual(discoverContinuumReferenceInventory(app, sampler).items, []);
});

test("persistent Reference Shelf replacements change saved source identity without exposing filenames", () => {
  const sampler = samplerNode();
  const conveyor = conveyorNode({
    referenceSlots: [ref("alpha"), ...Array(7).fill(null)],
  });
  const { app, connect } = graphWith([sampler, conveyor]);
  connect(conveyor, 6, sampler, "reference_image_1");

  const first = discoverContinuumReferenceInventory(app, sampler);
  const firstIdentity = first.items[0].source_identity;
  assert.match(firstIdentity, /^image-conveyor-ref-v1:[0-9a-f]{16}$/);
  assert.equal(firstIdentity.includes("alpha"), false);

  const stateWidget = conveyor.widgets.find((entry) => entry.name === "state_json");
  const state = JSON.parse(stateWidget.value);
  state.reference_slots[0] = ref("beta");
  stateWidget.value = JSON.stringify(state);

  const second = discoverContinuumReferenceInventory(app, sampler);
  assert.match(second.items[0].source_identity, /^image-conveyor-ref-v1:[0-9a-f]{16}$/);
  assert.notEqual(second.items[0].source_identity, firstIdentity);
  assert.equal(sameContinuumReferenceInventory(first, second), false);
});

test("queue-group outputs remain intentionally dynamic and do not fingerprint queue members", () => {
  const sampler = samplerNode();
  const conveyor = conveyorNode({
    outputMode: "queue_group",
    imagesPerExecution: 3,
  });
  const { app, connect } = graphWith([sampler, conveyor]);
  connect(conveyor, 0, sampler, "first_frame");
  connect(conveyor, 6, sampler, "reference_image_1");
  connect(conveyor, 7, sampler, "reference_image_2");

  const inventory = discoverContinuumReferenceInventory(app, sampler);
  assert.equal(inventory.items.length, 3);
  assert.ok(inventory.items.every((item) => !Object.hasOwn(item, "source_identity")));
});

test("non-Conveyor image sources retain ordinary wire-based discovery", () => {
  const sampler = samplerNode();
  const loader = {
    id: 50,
    type: "LoadImage",
    inputs: [],
    outputs: [{ name: "IMAGE", type: "IMAGE", links: [] }],
    widgets: [],
  };
  const { app, connect } = graphWith([sampler, loader]);
  connect(loader, 0, sampler, "reference_image_4");

  const inventory = discoverContinuumReferenceInventory(app, sampler);
  assert.equal(inventory.items.length, 1);
  assert.equal(inventory.items[0].tag, "<Picture 1>");
  assert.equal(inventory.items[0].source_node_class, "LoadImage");
});

test("unreadable Image Conveyor state fails closed instead of inventing public identities", () => {
  const sampler = samplerNode();
  const conveyor = conveyorNode();
  conveyor.widgets[0].value = "{broken";
  delete conveyor.__bil;
  const { app, connect } = graphWith([sampler, conveyor]);
  connect(conveyor, 6, sampler, "reference_image_1");

  assert.throws(
    () => discoverContinuumReferenceInventory(app, sampler),
    /no readable state_json/,
  );
});
