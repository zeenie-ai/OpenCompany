import React from 'react';
import { Rocket, Plug, Move, Zap } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';

const features = [
  { Icon: Plug, label: '108+ Nodes', desc: 'Workflow building blocks' },
  { Icon: Rocket, label: '6 AI Providers', desc: 'OpenAI, Claude, Gemini & more' },
  { Icon: Move, label: 'Drag & Drop', desc: 'Visual workflow builder' },
  { Icon: Zap, label: 'Real-time', desc: 'Live execution & monitoring' },
];

const WelcomeStep: React.FC = () => {
  return (
    <div className="py-2 text-center">
      <Rocket className="mx-auto mb-2 h-10 w-10 text-node-agent" />

      <h3 className="m-0 mb-1 text-xl font-semibold">Welcome to OpenCompany</h3>

      <p className="text-[15px] font-semibold text-node-agent">
        Visual workflow automation powered by AI agents
      </p>

      <p className="mx-auto mt-4 mb-6 max-w-[480px] text-sm text-muted-foreground">
        OpenCompany lets you build intelligent automation workflows by connecting
        AI models, tools, and services together on an interactive canvas.
        No coding required for most tasks.
      </p>

      <div className="mx-auto grid max-w-[440px] grid-cols-2 gap-3">
        {features.map(({ Icon, label, desc }) => (
          <Card
            key={label}
            className="text-center bg-node-agent-soft border-node-agent-border"
          >
            <CardContent className="flex flex-col items-center gap-1 p-3">
              <Icon className="h-5 w-5 text-node-agent" />
              <span className="block text-sm font-semibold">{label}</span>
              <span className="text-xs text-muted-foreground">{desc}</span>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
};

export default WelcomeStep;
