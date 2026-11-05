import assert from "node:assert/strict";
import { test } from "node:test";
import { boxAt, colorText, copyName, fitScale, matchesTemplate, moved, nameProblem, rectBetween, resized } from "../src/markup.ts";
import type { TemplateImage } from "../src/types.ts";

const image = (name: string, categories: string[]): TemplateImage => ({ name, url: "", modified: 0, categories });

test("the image fits the window", () => {
  assert.equal(fitScale(1000, 500, 500, 500), 0.5);
  assert.equal(fitScale(100, 100, 400, 200), 2);
  assert.equal(fitScale(0, 0, 100, 100), 1);
});

test("a drawn box stays inside the image, whichever way it was dragged", () => {
  assert.deepEqual(rectBetween([50, 40], [10, 10], 100, 100), [10, 10, 40, 30]);
  assert.deepEqual(rectBetween([90, 90], [120, 130], 100, 100), [90, 90, 10, 10]);
  assert.deepEqual(rectBetween([-5, -5], [5, 5], 100, 100), [0, 0, 5, 5]);
});

test("moving keeps the size inside the image", () => {
  assert.deepEqual(moved([10, 10, 20, 20], 5, -5, 100, 100), [15, 5, 20, 20]);
  assert.deepEqual(moved([10, 10, 20, 20], 500, 500, 100, 100), [80, 80, 20, 20]);
  assert.deepEqual(moved([10, 10, 20, 20], -500, -500, 100, 100), [0, 0, 20, 20]);
});

test("resizing moves only the dragged edges and never flips the box", () => {
  assert.deepEqual(resized([10, 10, 20, 20], "se", 5, 10, 100, 100), [10, 10, 25, 30]);
  assert.deepEqual(resized([10, 10, 20, 20], "nw", 5, 5, 100, 100), [15, 15, 15, 15]);
  assert.deepEqual(resized([10, 10, 20, 20], "e", -500, 0, 100, 100), [10, 10, 3, 20]);
  assert.deepEqual(resized([10, 10, 20, 20], "n", 0, -500, 100, 100), [10, 0, 20, 30]);
});

test("the topmost box answers at a point", () => {
  const boxes = [{ category: "a", bbox: [0, 0, 10, 10] as [number, number, number, number] }, { category: "b", bbox: [5, 5, 10, 10] as [number, number, number, number] }];
  assert.equal(boxAt(boxes, 7, 7), 1);
  assert.equal(boxAt(boxes, 1, 1), 0);
  assert.equal(boxAt(boxes, 50, 50), -1);
});

test("a copy gets a free name", () => {
  assert.equal(copyName("a", new Set(["a"])), "a_copy");
  assert.equal(copyName("a", new Set(["a", "a_copy", "a_copy_1"])), "a_copy_2");
});

test("one name marks one template across all images", () => {
  const boxes = [{ category: "here", bbox: [0, 0, 5, 5] as [number, number, number, number] }];
  const images = [image("0.png", ["here"]), image("1.png", ["there"])];
  assert.deepEqual(nameProblem(" ", "", boxes, images, "0.png"), { kind: "required" });
  assert.equal(nameProblem("here", "here", boxes, images, "0.png"), null);
  assert.deepEqual(nameProblem("here", "other", boxes, images, "0.png"), { kind: "taken", owner: "0.png" });
  assert.deepEqual(nameProblem("there", "", boxes, images, "0.png"), { kind: "taken", owner: "1.png" });
  assert.equal(nameProblem("new", "", boxes, images, "0.png"), null);
});

test("templates are found by picture or box name", () => {
  const item = image("7.png", ["Start_Button"]);
  assert.ok(matchesTemplate(item, ""));
  assert.ok(matchesTemplate(item, "7.P"));
  assert.ok(matchesTemplate(item, "start"));
  assert.ok(!matchesTemplate(item, "stop"));
  assert.equal(colorText(1, 2, 3), "rgb(1, 2, 3)");
});
