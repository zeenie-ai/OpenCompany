import * as React from "react"

import { cn } from "@/lib/utils"
import { Sounds } from "@/lib/sound"

function Textarea({ className, onChange, ...props }: React.ComponentProps<"textarea">) {
  // Fire the per-theme `type` sound on every keystroke (throttled by
  // the W19 last-fire window inside the engine).
  const handleChange = onChange
    ? (event: React.ChangeEvent<HTMLTextAreaElement>) => {
        Sounds.play('type');
        onChange(event);
      }
    : undefined;

  return (
    <textarea
      data-slot="textarea"
      onChange={handleChange}
      // `input` co-class shares per-theme decorations + W18 type delegate
      // with the Input primitive.
      className={cn(
        "input flex field-sizing-content min-h-16 w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-base transition-colors outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:bg-input/50 disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:disabled:bg-input/80 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
        className
      )}
      {...props}
    />
  )
}

export { Textarea }
