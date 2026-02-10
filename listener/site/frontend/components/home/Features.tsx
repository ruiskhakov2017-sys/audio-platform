'use client';

import { Volume2, Shield, Crown } from 'lucide-react';
import GlassCard from '@/components/ui/GlassCard';

const items = [
  {
    icon: Volume2,
    title: 'Cinematic Sound',
    text: 'Погружающий звук и профессиональная озвучка для полного погружения.',
  },
  {
    icon: Shield,
    title: 'Privacy',
    text: 'Конфиденциальность и анонимность. Только вы и звук.',
  },
  {
    icon: Crown,
    title: 'Exclusive',
    text: 'Эксклюзивные записи и премиум-контент от лучших дикторов.',
  },
];

export default function Features() {
  return (
    <section className="py-20">
      <div className="mx-auto w-full max-w-6xl px-6 sm:px-8 lg:px-12">
        <div className="grid gap-8 md:grid-cols-3">
          {items.map(({ icon: Icon, title, text }) => (
            <GlassCard key={title} className="flex flex-col items-center text-center">
              <div className="mb-4 flex h-20 w-20 shrink-0 items-center justify-center rounded-[2rem] border border-[#00B4D8]/30 bg-[#00B4D8]/10 text-[#00B4D8] backdrop-blur-xl">
                <Icon className="h-10 w-10 shrink-0" strokeWidth={1.5} />
              </div>
              <h3 className="text-lg font-bold text-white">{title}</h3>
              <p className="mt-2 text-sm text-zinc-400">{text}</p>
            </GlassCard>
          ))}
        </div>
      </div>
    </section>
  );
}
