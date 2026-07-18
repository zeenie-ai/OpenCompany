import React from 'react';
import { LayoutGrid, Bot, MessageCircle } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { useNodeGroups } from '../../../lib/nodeSpec';
import { NODE_ROLE_CLASSES, type NodeRole } from '../nodeRoleClasses';

const concepts: { Icon: typeof LayoutGrid; title: string; desc: string; role: NodeRole }[] = [
  {
    Icon: LayoutGrid,
    title: 'Snap blocks together',
    desc: 'Every project is made of simple blocks — a chat window, an AI brain, a web search. Drag them in from the right-hand panel and draw lines to connect them.',
    role: 'model',
  },
  {
    Icon: Bot,
    title: 'Agents do the thinking',
    desc: 'An agent is a block with a brain. Tell it what you want in plain English and it plans the steps, using the blocks connected to it.',
    role: 'agent',
  },
  {
    Icon: MessageCircle,
    title: 'Chat to make it go',
    desc: 'Press the green Start button at the top, then type in the chat panel at the bottom. Your agent answers right there.',
    role: 'trigger',
  },
];

const HowItWorksStep: React.FC = () => {
  const groupsQuery = useNodeGroups();
  const normalGroupLabels = Object.values(groupsQuery.data ?? {})
    .filter((group) => group.visibility === 'normal' || group.visibility === 'all')
    .map((group) => group.label);

  return (
    <div className="py-1">
      <div className="mb-4 text-center">
        <h4 className="m-0 mb-1 text-lg font-semibold">See how it works</h4>
        <p className="text-xs text-muted-foreground">Three ideas are all you need.</p>
      </div>

      <div className="flex w-full flex-col gap-2.5">
        {concepts.map((c) => {
          const classes = NODE_ROLE_CLASSES[c.role];
          return (
            <div
              key={c.title}
              className={`flex items-start gap-3 rounded-md border p-3 ${classes.card}`}
            >
              <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md ${classes.card}`}>
                <c.Icon className={`h-4 w-4 ${classes.text}`} />
              </div>
              <div className="min-w-0 flex-1">
                <div className={`mb-0.5 text-sm font-semibold ${classes.text}`}>{c.title}</div>
                <p className="m-0 text-xs leading-relaxed text-muted-foreground">{c.desc}</p>
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-4 text-center text-xs text-muted-foreground">
        <span>
          The Normal / Dev switch in the toolbar keeps things simple. Normal shows just the AI blocks
        </span>
        {normalGroupLabels.length > 0 && (
          <span className="mx-1 inline-flex flex-wrap items-center justify-center gap-1 align-middle">
            {normalGroupLabels.map((label) => (
              <Badge key={label} variant="outline" className="text-[10px]">
                {label}
              </Badge>
            ))}
          </span>
        )}
        <span> — flip to Dev when you want everything else.</span>
      </div>
    </div>
  );
};

export default HowItWorksStep;
