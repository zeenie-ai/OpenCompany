import * as React from "react"
import { Slider as SliderPrimitive } from "radix-ui"

import { cn } from "@/lib/utils"

function Slider({
  className,
  defaultValue,
  value,
  min = 0,
  max = 100,
  ...props
}: React.ComponentProps<typeof SliderPrimitive.Root>) {
  const _values = React.useMemo(
    () =>
      Array.isArray(value)
        ? value
        : Array.isArray(defaultValue)
          ? defaultValue
          : [min, max],
    [value, defaultValue, min, max]
  )

  return (
    <SliderPrimitive.Root
      data-slot="slider"
      defaultValue={defaultValue}
      value={value}
      min={min}
      max={max}
      className={cn(
        "relative flex w-full touch-none items-center select-none data-disabled:opacity-50 data-vertical:h-full data-vertical:min-h-40 data-vertical:w-auto data-vertical:flex-col",
        className
      )}
      {...props}
    >
      <SliderPrimitive.Track
        data-slot="slider-track"
        className="relative grow overflow-hidden rounded-full bg-muted data-horizontal:h-1 data-horizontal:w-full data-vertical:h-full data-vertical:w-1"
      >
        <SliderPrimitive.Range
          data-slot="slider-range"
          className="absolute bg-primary select-none data-horizontal:h-full data-vertical:w-full"
        />
      </SliderPrimitive.Track>
      {Array.from({ length: _values.length }, (_, index) => (
        <SliderPrimitive.Thumb
          data-slot="slider-thumb"
          key={index}
          className="relative block size-3 shrink-0 rounded-full border border-ring bg-white ring-ring/50 transition-[color,box-shadow] select-none after:absolute after:-inset-2 hover:ring-3 focus-visible:ring-3 focus-visible:outline-hidden active:ring-3 disabled:pointer-events-none disabled:opacity-50"
        />
      ))}
    </SliderPrimitive.Root>
  )
}

/*
 * LevelSlider — a few named levels on one pill track (design handoff chat
 * v2, the model picker's thinking slider). Radix supplies the slider role,
 * the keys (arrows, Home, End), pointer capture and snapping to a level; the
 * fill and the stops are drawn here, because the fill reaches the knob's far
 * edge rather than its centre. The knob and the fill glide on the spring
 * curve, quicker while dragged.
 */
function LevelSlider({
  className,
  value,
  count,
  onValueChange,
  label,
  valueText,
  ...props
}: Omit<
  React.ComponentProps<typeof SliderPrimitive.Root>,
  "value" | "defaultValue" | "onValueChange" | "min" | "max" | "step"
> & {
  /** The level shown, from 0. */
  value: number
  /** How many levels there are. */
  count: number
  onValueChange: (value: number) => void
  /** The slider's name (on the knob, which is the focusable slider). */
  label: string
  /** The level said aloud. */
  valueText: string
}) {
  const [dragging, setDragging] = React.useState(false)
  const last = count - 1
  const at = (index: number) => (last > 0 ? index / last : 0)
  return (
    <SliderPrimitive.Root
      data-slot="level-slider"
      data-dragging={dragging ? "" : undefined}
      value={[value]}
      min={0}
      max={last}
      step={1}
      onValueChange={([next]) => onValueChange(next)}
      onPointerDown={() => setDragging(true)}
      onPointerUp={() => setDragging(false)}
      onLostPointerCapture={() => setDragging(false)}
      className={cn(
        "group/level relative flex h-7.5 w-full cursor-pointer touch-none items-center rounded-pill select-none [--knob:--spacing(7.5)] has-focus-visible:ring-3 has-focus-visible:ring-action-save-soft data-disabled:opacity-50",
        // Radix places the knob's holder by `left`: it glides there.
        "[&>span:has(>[data-slot=level-slider-thumb])]:transition-[left] [&>span:has(>[data-slot=level-slider-thumb])]:duration-(--dur-slow) [&>span:has(>[data-slot=level-slider-thumb])]:ease-spring data-dragging:[&>span:has(>[data-slot=level-slider-thumb])]:duration-(--dur-level-drag) motion-reduce:[&>span]:transition-none",
        className
      )}
      {...props}
    >
      <SliderPrimitive.Track
        data-slot="level-slider-track"
        className="relative h-full grow overflow-hidden rounded-pill bg-bg-active"
      >
        <span
          aria-hidden
          className="absolute inset-y-0 left-0 rounded-pill bg-primary transition-[width] duration-(--dur-slow) ease-spring group-data-dragging/level:duration-(--dur-level-drag) motion-reduce:transition-none"
          style={{ width: `calc(var(--knob) + (100% - var(--knob)) * ${at(value)})` }}
        />
        {Array.from({ length: count }, (_, index) => (
          <span
            key={index}
            aria-hidden
            data-on={index <= value ? "" : undefined}
            className="absolute top-1/2 -mt-0.5 -ml-0.5 size-1 rounded-full bg-fg-faint data-on:bg-(--slider-stop-on)"
            style={{ left: `calc(var(--knob) / 2 + (100% - var(--knob)) * ${at(index)})` }}
          />
        ))}
      </SliderPrimitive.Track>
      <SliderPrimitive.Thumb
        data-slot="level-slider-thumb"
        aria-label={label}
        aria-valuetext={valueText}
        className="relative block size-(--knob) rounded-full outline-none after:absolute after:inset-px after:rounded-full after:bg-primary-foreground after:shadow-knob"
      />
    </SliderPrimitive.Root>
  )
}

export { LevelSlider, Slider }
