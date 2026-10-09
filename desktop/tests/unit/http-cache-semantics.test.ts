import { createRequire } from "node:module";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Exercise the dependency actually used by electron-builder, including its Bun patch.
let dependencyRequire = createRequire(import.meta.url);
for (const packageName of ["electron-builder", "app-builder-lib", "@electron/get", "got", "cacheable-request"]) {
  dependencyRequire = createRequire(dependencyRequire.resolve(packageName));
}
const CachePolicy = dependencyRequire("http-cache-semantics");
const now = Date.parse("2026-10-09T00:00:00Z");
type Headers = Record<string, string>;

const request = (headers: Headers = {}) => ({
  url: "https://cache-fixture.example/account",
  method: "GET",
  headers: { host: "cache-fixture.example", ...headers },
});

function policyFor(headers: Headers, requestHeaders: Headers = {}, shared = true) {
  const policy = new CachePolicy(request(requestHeaders), {
    status: 200,
    headers: { date: new Date(now).toUTCString(), ...headers },
  }, { shared });
  policy.now = () => now + 60_000;
  return policy;
}

const restricted: [string, Headers, Headers?][] = [
  ["shared Set-Cookie", { "cache-control": "max-age=600", "set-cookie": "fixture-user=alice" }],
  ["shared proxy-revalidate", { "cache-control": "max-age=600, proxy-revalidate" }],
  ["must-revalidate", { "cache-control": "max-age=30, must-revalidate" }],
  ["shared private", { "cache-control": "max-age=600, private" }],
  ["response no-store", { "cache-control": "max-age=600, no-store" }],
  ["response no-cache", { "cache-control": "max-age=600, no-cache" }],
  ["Vary wildcard", { "cache-control": "max-age=600", vary: "*" }],
  ["request no-store", { "cache-control": "max-age=600" }, { "cache-control": "no-store" }],
  ["shared authenticated", { "cache-control": "max-age=600" }, { authorization: "fixture-user alice" }],
];

beforeEach(() => vi.spyOn(Date, "now").mockReturnValue(now));
afterEach(() => vi.restoreAllMocks());

describe("http-cache-semantics shared-cache disclosure regression", () => {
  for (const [label, headers, requestHeaders] of restricted) {
    for (const maxStale of ["max-stale", "max-stale=3600"]) {
      it(`${label} cannot be reused with ${maxStale}, including after serialization`, () => {
        const original = policyFor(headers, requestHeaders);
        const restored = CachePolicy.fromObject(JSON.parse(JSON.stringify(original.toObject())));
        restored.now = original.now;
        for (const policy of [original, restored]) {
          const incoming = request({ ...requestHeaders, "cache-control": maxStale });
          const result = policy.evaluateRequest(incoming);
          expect(result.response).toBeUndefined();
          expect(result.revalidation?.synchronous).toBe(true);
          expect(policy.satisfiesWithoutRevalidation(incoming)).toBe(false);
        }
      });
    }

    it(`${label} cannot bypass restrictions with stale error or background revalidation`, () => {
      const policy = policyFor({
        ...headers,
        "cache-control": `${headers["cache-control"]}, stale-if-error=3600, stale-while-revalidate=3600`,
      }, requestHeaders);
      expect(policy._useStaleIfError()).toBe(false);
      expect(policy.useStaleWhileRevalidate()).toBe(false);
      const result = policy.revalidatedPolicy(request(requestHeaders), { status: 503, headers: {} });
      expect(result.policy).not.toBe(policy);
      expect(result.matches).toBe(false);
      expect(result.modified).toBe(true);
    });
  }

  it.each([
    ["fresh public", "public, max-age=600", {}, true, {}],
    ["stale public", "public, max-age=30", {}, true, { "cache-control": "max-stale=3600" }],
    ["public zero lifetime", "public, max-age=0", {}, true, { "cache-control": "max-stale=3600" }],
    ["private cache cookies", "max-age=600", { "set-cookie": "fixture-user=alice" }, false, {}],
    ["explicit public cookies", "public, max-age=600", { "set-cookie": "fixture-user=alice" }, true, {}],
    ["explicit immutable cookies", "immutable, max-age=600", { "set-cookie": "fixture-user=alice" }, true, {}],
  ] as [string, string, Headers, boolean, Headers][])(
    "preserves %s responses",
    (_label, cacheControl, headers, shared, requestHeaders) => {
      const policy = policyFor({ "cache-control": cacheControl, ...headers }, {}, shared);
      const result = policy.evaluateRequest(request(requestHeaders));
      expect(result.response).toBeDefined();
      expect(result.revalidation).toBeUndefined();
      expect(policy.satisfiesWithoutRevalidation(request(requestHeaders))).toBe(true);
    },
  );

  it("preserves stale public background revalidation and error fallback", () => {
    const policy = policyFor({ "cache-control": "public, max-age=30, stale-if-error=3600, stale-while-revalidate=3600" });
    expect(policy._useStaleIfError()).toBe(true);
    expect(policy.useStaleWhileRevalidate()).toBe(true);
    const result = policy.evaluateRequest(request());
    expect(result.response).toBeDefined();
    expect(result.revalidation?.synchronous).toBe(false);
    expect(policy.revalidatedPolicy(request(), { status: 503, headers: {} }).policy).toBe(policy);
  });

  it.each(["URL", "Vary principal"])("does not use error fallback for a different %s", (mismatch) => {
    const alice = { authorization: "fixture-user alice" };
    const policy = policyFor({
      "cache-control": "public, max-age=30, stale-if-error=3600",
      vary: "authorization",
    }, alice);
    expect(policy._useStaleIfError()).toBe(true);
    const incoming = mismatch === "URL"
      ? { ...request(alice), url: "https://cache-fixture.example/other-account" }
      : request({ authorization: "fixture-user bob" });
    const result = policy.revalidatedPolicy(incoming, { status: 503, headers: {} });
    expect(result.policy).not.toBe(policy);
    expect(result.matches).toBe(false);
    expect(result.modified).toBe(true);
  });

  it("keeps incoming request no-cache restricted", () => {
    const policy = policyFor({ "cache-control": "public, max-age=600" });
    expect(policy.evaluateRequest(request({ "cache-control": "no-cache, max-stale" })).response).toBeUndefined();
  });

  it("does not reuse an authenticated Vary entry for a different principal", () => {
    const policy = policyFor({ "cache-control": "public, max-age=600", vary: "authorization" }, { authorization: "fixture-user alice" });
    const result = policy.evaluateRequest(request({ authorization: "fixture-user bob", "cache-control": "max-stale" }));
    expect(result.response).toBeUndefined();
    expect(result.revalidation?.synchronous).toBe(true);
  });
});
