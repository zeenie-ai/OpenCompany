import * as React from "react"

import { cn } from "@/lib/utils"
import { Sounds } from "@/lib/sound"

function Input({ className, type, onChange, ...props }: React.ComponentProps<"input">) {
  // Fire the per-theme `type` sound on every keystroke. The engine
  // throttles `type` internally (W19 30 ms last-fire window) so rapid
  // typing into a long input doesn't queue dozens of OscillatorNodes.
  // No-op when sound is disabled.
  const handleChange = onChange
    ? (event: React.ChangeEvent<HTMLInputElement>) => {
        Sounds.play('type');
        onChange(event);
      }
    : undefined;

  return (
    <input
      type={type}
      data-slot="input"
      onChange={handleChange}
      // `input` co-class is the design-handoff structural hook —
      // per-theme CSS attaches ruled-line backgrounds (Renaissance),
      // terminal prompt prefix (Cyber), washi underline (Edo), etc.
      // Also targeted by the W18 `type` sound delegate.
      className={cn(
        "input h-8 w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-1 text-base transition-colors outline-none file:inline-flex file:h-6 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:bg-input/50 disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:disabled:bg-input/80 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
        className
      )}
      {...props}
    />
  )
}

export { Input }
