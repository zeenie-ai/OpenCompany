// The parent sends the trigger after reading the host's first operation.
// This lets a slow SDK startup finish before exercising active shutdown.
process.once("message", (trigger) => {
  process.disconnect?.();
  if (trigger === "terminate") {
    process.emit("SIGTERM");
  } else if (trigger === "expired-clock") {
    const now = performance.now.bind(performance);
    Object.defineProperty(performance, "now", {value: () => now() + 121_000});
  } else {
    throw new Error("Unknown test interrupt");
  }
});
