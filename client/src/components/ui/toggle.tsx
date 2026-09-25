/* eslint-disable react-refresh/only-export-components -- shadcn primitive: cva variants + component export co-located by convention. */
import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Toggle as TogglePrimitive } from "radix-ui"

import { cn } from "@/lib/utils"
import { Sounds } from "@/lib/sound"

/*
 * Toggle — a two-state button (shadcn, on the repo's tokens).
 *
 * Variants are the three looks the design system uses for pressed state:
 * - `default`: quiet text button; pressed reads as a raised surface.
 * - `segmented`: an item inside a ToggleGroup track (the gen-UI Choice and
 *   the Normal/Dev switch). The track itself is drawn by ToggleGroup.
 * - `chips`: a bordered pill (category filters); pressed takes the agent
 *   role's soft tint. Callers may pass a different role tint in className.
 */
const toggleVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-1.5 border border-transparent font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 motion-reduce:transition-none [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-3",
  {
    variants: {
      variant: {
        default:
          "rounded-md bg-transparent text-fg-muted hover:bg-bg-hover hover:text-fg-default data-[state=on]:bg-bg-elevated data-[state=on]:text-fg-default",
        segmented:
          "rounded-[calc(var(--radius-row)-3px)] bg-transparent text-fg-muted hover:text-fg-default data-[state=on]:bg-bg-elevated data-[state=on]:text-fg-default data-[state=on]:shadow-sm",
        chips:
          "rounded-pill border-border-default bg-transparent text-fg-muted hover:bg-bg-hover hover:text-fg-default data-[state=on]:border-node-agent-edge data-[state=on]:bg-node-agent-fill data-[state=on]:text-node-agent-ink",
      },
      size: {
        sm: "h-6.5 px-2.5 text-xs font-semibold",
        default: "h-7 px-3 text-meta",
        lg: "h-8 px-3 text-xs",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Toggle({
  className,
  variant,
  size,
  onClick,
  ...props
}: React.ComponentProps<typeof TogglePrimitive.Root> & VariantProps<typeof toggleVariants>) {
  return (
    <TogglePrimitive.Root
      data-slot="toggle"
      className={cn(toggleVariants({ variant, size, className }))}
      onClick={(event) => {
        Sounds.play('click')
        onClick?.(event)
      }}
      {...props}
    />
  )
}

export { Toggle, toggleVariants }
