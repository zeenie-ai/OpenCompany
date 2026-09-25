import * as React from "react"
import { type VariantProps } from "class-variance-authority"
import { ToggleGroup as ToggleGroupPrimitive } from "radix-ui"

import { cn } from "@/lib/utils"
import { Sounds } from "@/lib/sound"
import { toggleVariants } from "@/components/ui/toggle"

/*
 * ToggleGroup — a set of toggles with single or multiple selection
 * (shadcn, on the repo's tokens). Radix supplies roving focus and the
 * radiogroup / toolbar semantics; items inherit variant and size from the
 * group.
 *
 * `segmented` draws the track (the gen-UI Choice, the Normal/Dev switch):
 * items sit 3px inside it, so their radius is the track's minus 3px.
 * `chips` lays the pills out with a gap and no track.
 */
const GROUP_VARIANT = {
  default: "gap-1",
  segmented: "gap-0.5 rounded-row border border-border-default bg-bg-app p-0.75",
  chips: "flex-wrap gap-1.5",
} as const

type ToggleGroupVariant = keyof typeof GROUP_VARIANT
type ToggleSize = VariantProps<typeof toggleVariants>["size"]

const ToggleGroupContext = React.createContext<{ variant: ToggleGroupVariant; size: ToggleSize }>({
  variant: "default",
  size: "default",
})

function ToggleGroup({
  className,
  variant = "default",
  size = "default",
  children,
  ...props
}: React.ComponentProps<typeof ToggleGroupPrimitive.Root> & {
  variant?: ToggleGroupVariant
  size?: ToggleSize
}) {
  const context = React.useMemo(() => ({ variant, size }), [variant, size])
  return (
    <ToggleGroupPrimitive.Root
      data-slot="toggle-group"
      data-variant={variant}
      data-size={size}
      className={cn("group/toggle-group flex w-fit items-center", GROUP_VARIANT[variant], className)}
      {...props}
    >
      <ToggleGroupContext.Provider value={context}>{children}</ToggleGroupContext.Provider>
    </ToggleGroupPrimitive.Root>
  )
}

function ToggleGroupItem({
  className,
  children,
  onClick,
  ...props
}: React.ComponentProps<typeof ToggleGroupPrimitive.Item>) {
  const { variant, size } = React.useContext(ToggleGroupContext)
  return (
    <ToggleGroupPrimitive.Item
      data-slot="toggle-group-item"
      data-variant={variant}
      data-size={size}
      className={cn(toggleVariants({ variant, size }), "focus:z-10 focus-visible:z-10", className)}
      onClick={(event) => {
        Sounds.play('click')
        onClick?.(event)
      }}
      {...props}
    >
      {children}
    </ToggleGroupPrimitive.Item>
  )
}

export { ToggleGroup, ToggleGroupItem }
