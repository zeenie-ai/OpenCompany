import * as React from "react"

import { cn } from "@/lib/utils"
import { Sounds } from "@/lib/sound"

/*
 * `default`: the bordered form field. The `input` co-class shares the
 * per-theme field decorations with the Input primitive.
 * `bare`: no chrome at all (no border, fill, focus ring, padding or `input`
 * decoration) for a textarea that sits inside its own surface, such as the
 * Normal-mode composer. The surrounding surface draws the focus state.
 */
const VARIANTS = {
  default:
    "input flex field-sizing-content min-h-16 w-full rounded-lg border border-input bg-transparent px-2.5 py-2 text-base transition-colors outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:bg-input/50 disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:disabled:bg-input/80 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
  bare:
    "flex field-sizing-content w-full resize-none bg-transparent p-0 outline-none placeholder:text-fg-faint disabled:cursor-not-allowed disabled:opacity-50",
} as const

function Textarea({
  className,
  onChange,
  variant = "default",
  ...props
}: React.ComponentProps<"textarea"> & { variant?: keyof typeof VARIANTS }) {
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
      className={cn(VARIANTS[variant], className)}
      {...props}
    />
  )
}

export { Textarea }
