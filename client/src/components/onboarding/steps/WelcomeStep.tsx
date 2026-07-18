import React from 'react';
import { Rocket, Bot, Blocks, Plug, ShieldCheck } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { NODE_ROLE_CLASSES, type NodeRole } from '../nodeRoleClasses';

const features: { Icon: typeof Bot; label: string; desc: string; role: NodeRole }[] = [
  { Icon: Bot, label: 'Agents that work for you', desc: 'Give them a goal; they figure out the steps.', role: 'agent' },
  { Icon: Blocks, label: 'Build by dragging', desc: 'Snap blocks together — no code, no setup scripts.', role: 'model' },
  { Icon: Plug, label: 'Bring your favorite AI', desc: 'Works with OpenAI, Claude, Gemini and more.', role: 'skill' },
  { Icon: ShieldCheck, label: 'Yours and private', desc: 'Everything runs and stays on this device.', role: 'workflow' },
];

const WelcomeStep: React.FC = () => {
  return (
    <div className="py-2 text-center">
      <Rocket className="mx-auto mb-2 h-10 w-10 text-node-agent" />

      <h3 className="m-0 mb-1 text-xl font-semibold">Build your own AI team</h3>

      <p className="text-[15px] font-semibold text-node-agent">
        Helpful AI agents that chat with you and get real work done
      </p>

      <p className="mx-auto mt-4 mb-6 max-w-[480px] text-sm text-muted-foreground">
        OpenCompany runs on your own computer. Snap building blocks together on
        a visual canvas, connect your favorite AI, and watch your agents get to
        work. No coding needed.
      </p>

      <div className="mx-auto grid max-w-[440px] grid-cols-2 gap-3">
        {features.map(({ Icon, label, desc, role }) => {
          const classes = NODE_ROLE_CLASSES[role];
          return (
            <Card key={label} className={`text-center ${classes.card}`}>
              <CardContent className="flex flex-col items-center gap-1 p-3">
                <Icon className={`h-5 w-5 ${classes.text}`} />
                <span className="block text-sm font-semibold">{label}</span>
                <span className="text-xs text-muted-foreground">{desc}</span>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
};

export default WelcomeStep;
