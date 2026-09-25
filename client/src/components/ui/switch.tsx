import * as React from "react"
import { Switch as SwitchPrimitive } from "radix-ui"

import { cn } from "@/lib/utils"

/*
 * Tones own the track and knob colours, so a tone's classes are never
 * layered over another's: the default tone's `dark:` rules out-specify a
 * plain `data-checked:` utility (shadcn's data-state variants use a
 * zero-specificity `:where()`), and would win in dark mode.
 *
 * `run`: the Normal-mode settings switch (design handoff) — soft run-green
 * track with the readable run ink as the knob when on; a neutral track and
 * a muted knob when off.
 */
const ROOT_TONE = {
  default: "data-checked:bg-primary data-unchecked:bg-input dark:data-unchecked:bg-input/80",
  run: "data-checked:border-action-run-border data-checked:bg-action-run-hover data-unchecked:border-border-strong data-unchecked:bg-border-default",
} as const

const THUMB_TONE = {
  default: "bg-background dark:data-checked:bg-primary-foreground dark:data-unchecked:bg-foreground",
  run: "data-checked:bg-action-run-ink data-unchecked:bg-fg-muted",
} as const

function Switch({
  className,
  size = "default",
  tone = "default",
  ...props
}: React.ComponentProps<typeof SwitchPrimitive.Root> & {
  /** `md`: 36x20 track, 12px knob travelling on the overshoot curve. */
  size?: "sm" | "default" | "md"
  tone?: keyof typeof ROOT_TONE
}) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      data-size={size}
      className={cn(
        "peer group/switch relative inline-flex shrink-0 items-center rounded-full border border-transparent transition-all outline-none after:absolute after:-inset-x-3 after:-inset-y-2 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 data-[size=default]:h-[18.4px] data-[size=default]:w-[32px] data-[size=sm]:h-[14px] data-[size=sm]:w-[24px] data-[size=md]:h-5 data-[size=md]:w-9 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 data-disabled:cursor-not-allowed data-disabled:opacity-50 motion-reduce:transition-none",
        ROOT_TONE[tone],
        className
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
        className={cn(
          "pointer-events-none block rounded-full ring-0 transition-transform group-data-[size=default]/switch:size-4 group-data-[size=sm]/switch:size-3 group-data-[size=default]/switch:data-checked:translate-x-[calc(100%-2px)] group-data-[size=sm]/switch:data-checked:translate-x-[calc(100%-2px)] group-data-[size=default]/switch:data-unchecked:translate-x-0 group-data-[size=sm]/switch:data-unchecked:translate-x-0 motion-reduce:transition-none!",
          // md: the knob sits 3px inside the track edge and travels 16px.
          // (`transition-none!` above: the md group variant out-specifies a
          // bare motion-reduce utility.)
          "group-data-[size=md]/switch:size-3 group-data-[size=md]/switch:transition-[translate,background-color] group-data-[size=md]/switch:duration-(--dur-switch) group-data-[size=md]/switch:ease-overshoot group-data-[size=md]/switch:data-unchecked:translate-x-[3px] group-data-[size=md]/switch:data-checked:translate-x-[19px]",
          THUMB_TONE[tone]
        )}
      />
    </SwitchPrimitive.Root>
  )
}

export { Switch }
