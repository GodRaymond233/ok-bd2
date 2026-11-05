import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { api, ApiError, errorText } from "../src/api.ts";

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

function respond(body: string, status: number): void {
  globalThis.fetch = async () => new Response(body, { status });
}

async function failure(call: () => Promise<unknown>): Promise<ApiError> {
  const reason = await call().then(() => assert.fail("the call should fail"), (error: unknown) => error);
  assert.ok(reason instanceof ApiError);
  return reason;
}

const NOT_CANCELLED = new AbortController().signal;

test("an error that is not JSON keeps its status and text on every path", async () => {
  respond("Internal Server Error", 500);
  for (const call of [() => api.info(), () => api.preview(NOT_CANCELLED)]) {
    const error = await failure(call);
    assert.equal(error.status, 500);
    assert.equal(error.message, "Internal Server Error");
  }
});

test("the core's detail is shown on every path", async () => {
  respond(JSON.stringify({ detail: "请先连接游戏" }), 400);
  for (const call of [() => api.info(), () => api.preview(NOT_CANCELLED)]) {
    const error = await failure(call);
    assert.equal(error.status, 400);
    assert.equal(error.message, "请先连接游戏");
  }
});

test("a rejected request reads as its messages, not as [object Object]", async () => {
  respond(JSON.stringify({ detail: [{ msg: "缺少字段" }, { msg: "类型错误" }, { code: 1 }] }), 422);
  assert.equal((await failure(() => api.info())).message, '缺少字段；类型错误；{"code":1}');
});

test("an empty error body falls back to the caller's text", async () => {
  respond("", 502);
  assert.equal((await failure(() => api.info())).message, "HTTP 502");
  assert.equal((await failure(() => api.preview(NOT_CANCELLED))).message, "无法读取游戏画面");
});

test("a success that is not JSON is an ApiError, not a SyntaxError", async () => {
  respond("<html>", 200);
  const error = await failure(() => api.info());
  assert.equal(error.status, 200);
  assert.equal(error.message, "后台服务返回的内容无法解析");
});

test("a success returns its data, and an empty one returns null", async () => {
  respond(JSON.stringify({ core_version: "0.9.7" }), 200);
  assert.deepEqual(await api.info(), { core_version: "0.9.7" });
  respond("", 200);
  assert.equal(await api.info(), null);
});

test("a cancelled request stays a cancellation", async () => {
  const cancelled = new DOMException("cancelled", "AbortError");
  globalThis.fetch = async () => {
    throw cancelled;
  };
  await assert.rejects(api.preview(NOT_CANCELLED), (error) => error === cancelled);
  await assert.rejects(api.info(), (error) => error === cancelled);
});

test("a caught error reads as its message, without the type in front", () => {
  assert.equal(errorText(new ApiError(400, "请先连接游戏")), "请先连接游戏");
  assert.equal(errorText("断开连接"), "断开连接");
});
