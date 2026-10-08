/**
 * Things to say first, in an empty chat that can take a message (a new
 * hire's first day, onboarding handoff C): chips that put their words in the
 * message box for the owner to send or change. They rise in a moment after
 * the chat can take them.
 */

import { useLayoutEffect, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { rise } from '@/lib/motion';

export function Greetings({ items, onPick }: { items: readonly string[]; onPick: (text: string) => void }) {
  const ref = useRef<HTMLUListElement>(null);
  useLayoutEffect(() => {
    rise(ref.current, { delay: 200 });
  }, []);
  return (
    <ul ref={ref} aria-label="Say hello" className="m-0 flex list-none flex-wrap justify-center gap-2 p-0">
      {items.map((text) => (
        <li key={text}>
          <Button variant="chip" size="chip" onClick={() => onPick(text)} className="bg-bg-elevated font-medium">
            {text}
          </Button>
        </li>
      ))}
    </ul>
  );
}

export default Greetings;
