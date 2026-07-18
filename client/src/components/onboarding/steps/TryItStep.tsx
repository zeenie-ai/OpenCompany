import React from 'react';
import { FolderOpen, Play, MessageCircle, Code, Users } from 'lucide-react';
import { NODE_ROLE_CLASSES, type NodeRole } from '../nodeRoleClasses';

const recipe: { Icon: typeof FolderOpen; title: string; desc: string; role: NodeRole }[] = [
  {
    Icon: FolderOpen,
    title: 'Open AI Assistant',
    desc: 'Find it in the sidebar on the left — a friendly helper that can search the web and manage files.',
    role: 'skill',
  },
  {
    Icon: Play,
    title: 'Press the green Start button',
    desc: "It's in the toolbar at the top. This wakes your agent up.",
    role: 'agent',
  },
  {
    Icon: MessageCircle,
    title: 'Say hello',
    desc: 'Type anything in the chat panel at the bottom of the screen. Your agent replies right there.',
    role: 'trigger',
  },
];

const explore: { Icon: typeof Code; title: string; desc: string; role: NodeRole }[] = [
  {
    Icon: Code,
    title: 'Claude Assistant',
    desc: 'An agent powered by Claude Code — great for coding help. Works best with an Anthropic key.',
    role: 'model',
  },
  {
    Icon: Users,
    title: 'AI Employee',
    desc: "A whole team of agents working together. Explore it once you're comfortable.",
    role: 'workflow',
  },
];

const TryItStep: React.FC = () => {
  return (
    <div className="py-1">
      <div className="mb-4 text-center">
        <h4 className="m-0 mb-1 text-lg font-semibold">Say hello to your first agent</h4>
        <p className="text-xs text-muted-foreground">
          Three examples are already waiting in your sidebar. Here&apos;s the fastest way in.
        </p>
      </div>

      <div className="flex w-full flex-col gap-2.5">
        {recipe.map((step, index) => {
          const classes = NODE_ROLE_CLASSES[step.role];
          return (
            <div
              key={step.title}
              className={`flex items-start gap-3 rounded-md border p-3 ${classes.card}`}
            >
              <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${classes.card}`}>
                <span className={`text-sm font-semibold ${classes.text}`}>{index + 1}</span>
              </div>
              <div className="min-w-0 flex-1">
                <div className={`mb-0.5 flex items-center gap-1.5 text-sm font-semibold ${classes.text}`}>
                  <step.Icon className="h-4 w-4" />
                  {step.title}
                </div>
                <p className="m-0 text-xs leading-relaxed text-muted-foreground">{step.desc}</p>
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-4 mb-2 text-center text-xs font-semibold text-muted-foreground">
        More to explore
      </div>
      <div className="grid grid-cols-2 gap-2.5">
        {explore.map((item) => {
          const classes = NODE_ROLE_CLASSES[item.role];
          return (
            <div key={item.title} className={`rounded-md border p-3 ${classes.card}`}>
              <div className={`mb-0.5 flex items-center gap-1.5 text-sm font-semibold ${classes.text}`}>
                <item.Icon className="h-4 w-4" />
                {item.title}
              </div>
              <p className="m-0 text-xs leading-relaxed text-muted-foreground">{item.desc}</p>
            </div>
          );
        })}
      </div>

      <p className="mt-4 text-center text-xs text-muted-foreground">
        You can replay this guide anytime from Settings.
      </p>
    </div>
  );
};

export default TryItStep;
