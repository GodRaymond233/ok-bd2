import assert from "node:assert/strict";
import { test } from "node:test";
import { dedentSelection, helperCall, helperGroups, indentSelection, insertLine, missingParam, newLine, pythonValue, tokenize, validClassName } from "../src/scripts.ts";
import type { ScriptHelper } from "../src/types.ts";

function helper(name: string, category: string, params: ScriptHelper["params"] = [], extra: Partial<ScriptHelper> = {}): ScriptHelper {
  return { name, category, params, doc: "", full_doc: `${name}()`, class_name: "BaseTask", is_static: false, ...extra };
}

test("the palette keeps its order, searches the docs and leaves out the keyboard", () => {
  const helpers = [helper("sleep", "Control"), helper("click", "Mouse", [], { full_doc: "click the box" }), helper("send_key", "Key")];
  assert.deepEqual(helperGroups(helpers, "").map((group) => group.name), ["Mouse", "Control"]);
  assert.deepEqual(helperGroups(helpers, "THE BOX").map((group) => group.helpers[0].name), ["click"]);
  assert.deepEqual(helperGroups(helpers, "send_key"), []);
});

test("a parameter is Python as written, and a bare word or Chinese text becomes a string", () => {
  assert.equal(pythonValue("0.5"), "0.5");
  assert.equal(pythonValue("True"), "True");
  assert.equal(pythonValue("'a b'"), "'a b'");
  assert.equal(pythonValue("self.box"), "self.box");
  assert.equal(pythonValue("(1, 2)"), "(1, 2)");
  assert.equal(pythonValue("name"), "'name'");
  assert.equal(pythonValue("确认"), "'确认'");
  assert.equal(pythonValue("a b"), "'a b'");
  assert.equal(pythonValue("it's"), "'it\\'s'");
});

test("a helper call names what has a default and leaves out what is empty", () => {
  const click = helper("click", "Mouse", [
    { name: "x", default: "-1", doc: "" },
    { name: "y", default: "-1", doc: "" },
  ]);
  assert.equal(helperCall(click, { x: "0.5", y: " " }), "self.click(x=0.5)");
  const find = helper("find", "Template Matching", [{ name: "label", default: null, doc: "" }], { is_static: true });
  assert.equal(helperCall(find, { label: "ok_button" }), "BaseTask.find('ok_button')");
  assert.equal(missingParam(find, {}), "label");
  assert.equal(missingParam(find, { label: "a" }), null);
});

test("a helper goes on its own line, at the right indent", () => {
  const source = "    def run(self):\n        pass\n";
  const below = insertLine(source, source.indexOf("pass"), "self.click()");
  assert.equal(below.text, "    def run(self):\n        pass\n        self.click()\n");
  assert.equal(below.start, below.text.indexOf("()\n") + 2);
  assert.equal(insertLine("a\n\nb", 2, "x()").text, "a\n        x()\nb");
  assert.equal(insertLine("a\n    \nb", 3, "x()").text, "a\n    x()\nb");
});

test("tab and shift+tab work on the caret and on selected lines", () => {
  assert.equal(indentSelection("ab", 1, 1).text, "a    b");
  const both = "a\nb\nc";
  const indented = indentSelection(both, 0, 3);
  assert.equal(indented.text, "    a\n    b\nc");
  assert.deepEqual([indented.start, indented.end], [4, 11]);
  const dedented = dedentSelection(indented.text, indented.start, indented.end);
  assert.equal(dedented.text, both);
  assert.equal(dedentSelection("  a", 3, 3).text, "a");
});

test("enter keeps the indent and goes deeper after a colon", () => {
  assert.equal(newLine("    a", 5, 5).text, "    a\n    ");
  assert.equal(newLine("def run(self):", 14, 14).text, "def run(self):\n    ");
});

test("the coloured runs join back to the source", () => {
  const source = "from ok import BaseTask\n\nclass A(BaseTask):\n    # note\n    def run(self):\n        x = 'a' + \"b\" + 12\n        '''doc\n        more'''\n";
  const tokens = tokenize(source);
  assert.equal(tokens.map((token) => token.text).join(""), source);
  const kinds = (kind: string) => tokens.filter((token) => token.kind === kind).map((token) => token.text);
  assert.ok(kinds("keyword").includes("class"));
  assert.deepEqual(kinds("name"), ["A", "run"]);
  assert.deepEqual(kinds("comment"), ["# note"]);
  assert.deepEqual(kinds("number"), ["12"]);
  assert.equal(kinds("string").length, 3);
});

test("a class name also names the file", () => {
  assert.ok(validClassName("My_Task2"));
  assert.ok(!validClassName("2Task"));
  assert.ok(!validClassName("my task"));
  assert.ok(!validClassName("任务"));
});
