import { clsx, type ClassValue } from "clsx"
import { extendTailwindMerge } from "tailwind-merge"

/**
 * tailwind-merge only knows Tailwind's default scale names, and it misreads
 * any other name: `text-meta` is a font size here (index.css `@theme inline`),
 * but tailwind-merge takes it for a text colour and drops the `text-fg-muted`
 * beside it; `rounded-panel` and `shadow-dialog` survive next to the
 * `rounded-lg` / `shadow-2xl` they were meant to replace. Every custom name
 * in those `@theme inline` namespaces is registered here, and
 * lib/__tests__/cn.test.ts fails when index.css gains one that is not.
 */
const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      text: ["meta", "row", "lead", "title", "hero"],
      radius: ["pill", "row", "card", "panel", "draft", "composer"],
      shadow: ["float", "popover", "dialog"],
      ease: ["spring", "overshoot", "reveal"],
      tracking: ["label", "hero", "wordmark"],
      leading: ["hero"],
      blur: ["scrim"],
    },
  },
})

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
