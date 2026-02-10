'use client';

import GlassCard from '@/components/ui/GlassCard';

const placeholderImage = 'https://images.unsplash.com/photo-1514525253440-b393452e8d26?q=80&w=1200&auto=format&fit=crop';

export default function AboutSection() {
  return (
    <section id="about" className="relative py-20">
      <div className="mx-auto w-full max-w-5xl px-6 sm:px-8 lg:px-12">
        <div className="relative overflow-hidden rounded-[2.5rem] border border-white/10">
          <div className="absolute inset-0">
            <img
              src={placeholderImage}
              alt=""
              className="h-full w-full object-cover grayscale"
            />
            <div className="absolute inset-0 bg-[#000814]/75" />
          </div>
          <div className="relative p-8 md:p-12 lg:p-16">
            <GlassCard className="border border-white/10 bg-[#001d3d]/60 backdrop-blur-xl">
              <h2 className="text-2xl font-black tracking-tight text-white md:text-3xl">
                Зачем вам это?
              </h2>
              <p className="mt-4 leading-relaxed text-zinc-400">
                Аудио эротика — это пространство для воображения и чувственности. В отличие от видео,
                здесь нет готовой картинки: вы сами рисуете образы, слышите голос и создаёте атмосферу.
                Звук обостряет ощущения и позволяет погрузиться глубже, чем любое изображение.
              </p>
            </GlassCard>
          </div>
        </div>
      </div>
    </section>
  );
}
