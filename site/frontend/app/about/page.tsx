'use client';

import { motion } from 'framer-motion';
import { Header } from '@/components/layout/Header';
import { Mic, FileText, Sparkles } from 'lucide-react';

const ACCENT = '#00B4D8';

const whyUs = [
  {
    icon: Mic,
    title: 'Голоса, которые оживают',
    text: 'Мы тщательно отбираем актеров и дикторов. Нам важно не просто прочитать текст, а передать каждое дыхание, каждое изменение тембра.',
  },
  {
    icon: FileText,
    title: 'Твой сценарий — наши правила',
    text: 'Мы — одна из немногих платформ, где ты можешь стать автором. Если у тебя есть сюжет, который не дает покоя, мы озвучим его специально для тебя, сохранив ту атмосферу, которую задумал именно ты.',
  },
  {
    icon: Sparkles,
    title: 'Эстетика в деталях',
    text: 'Мы не гонимся за количеством. Наша цель — собрать библиотеку историй, которые хочется переслушивать, каждый раз находя в них что-то новое.',
  },
];

export default function AboutPage() {
  return (
    <div className="min-h-screen bg-[#000814] text-white">
      <Header />

      <main className="relative z-10 pt-28 pb-24 px-6">
        <div className="max-w-4xl mx-auto">
          {/* Title */}
          <motion.h1
            className="font-heading text-4xl md:text-5xl lg:text-6xl font-bold text-white text-center leading-tight mb-16"
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6 }}
          >
            Dirty Secrets — Твои фантазии обретают голос
          </motion.h1>

          {/* Вступление */}
          <motion.section
            className="mb-16 text-center"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-50px' }}
            transition={{ duration: 0.5 }}
          >
            <h2 className="font-heading text-xl font-semibold text-[#00B4D8] uppercase tracking-wider mb-4">
              Вступление
            </h2>
            <p className="text-zinc-300 text-lg leading-relaxed max-w-2xl mx-auto">
              Мы создали Dirty Secrets для тех, кто понимает: самое мощное воображение — у нас в голове. В мире, перенасыщенном визуальным шумом, мы решили вернуть интимность. Звук не диктует тебе, что видеть. Он лишь направляет, позволяя твоему разуму дорисовывать самые сокровенные детали.
            </p>
          </motion.section>

          {/* Наша философия */}
          <motion.section
            className="mb-16 text-center"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-50px' }}
            transition={{ duration: 0.5 }}
          >
            <h2 className="font-heading text-xl font-semibold text-[#00B4D8] uppercase tracking-wider mb-4">
              Наша философия
            </h2>
            <p className="text-zinc-300 text-lg leading-relaxed max-w-2xl mx-auto">
              Dirty Secrets — это территория свободы от стереотипов. Мы верим, что каждый заслуживает качественный контент, который заставляет сердце биться чаще. Здесь нет места дешевым приемам — только эстетика, атмосфера и честные эмоции.
            </p>
          </motion.section>

          {/* Почему именно мы — grid with icons */}
          <motion.section
            className="mb-16"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-50px' }}
            transition={{ duration: 0.5 }}
          >
            <h2 className="font-heading text-xl font-semibold text-[#00B4D8] uppercase tracking-wider mb-10 text-center">
              Почему именно мы
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 max-w-6xl mx-auto">
              {whyUs.map((item, i) => (
                <motion.div
                  key={item.title}
                  className="rounded-2xl border border-white/10 bg-white/5 p-6 backdrop-blur-sm hover:border-[#00B4D8]/30 transition-colors flex flex-col"
                  initial={{ opacity: 0, y: 24 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ duration: 0.5, delay: i * 0.1 }}
                >
                  <div
                    className="w-12 h-12 rounded-xl flex items-center justify-center mb-4 shrink-0"
                    style={{ backgroundColor: `${ACCENT}20`, border: `1px solid ${ACCENT}40` }}
                  >
                    <item.icon className="w-6 h-6" style={{ color: ACCENT }} strokeWidth={1.5} />
                  </div>
                  <h3 className="text-lg font-bold text-white mb-2">{item.title}</h3>
                  <p className="text-zinc-400 text-sm leading-relaxed flex-1">{item.text}</p>
                </motion.div>
              ))}
            </div>
          </motion.section>

          {/* Наш подход */}
          <motion.section
            className="mb-16 text-center"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-50px' }}
            transition={{ duration: 0.5 }}
          >
            <h2 className="font-heading text-xl font-semibold text-[#00B4D8] uppercase tracking-wider mb-4">
              Наш подход
            </h2>
            <p className="text-zinc-300 text-lg leading-relaxed max-w-2xl mx-auto mb-6">
              Мы ценим твое время и твой выбор. Dirty Secrets — это полностью независимый проект. Мы развиваемся вместе с нашими слушателями и всегда открыты к вашим идеям. Здесь ты не просто пользователь, ты — часть закрытого клуба любителей качественной аудио-литературы.
            </p>
            <p className="text-zinc-400 text-lg leading-relaxed italic max-w-2xl mx-auto">
              Закрой глаза. Нажми на Play. Позволь себе услышать то, о чем другие только шепчутся.
            </p>
          </motion.section>
        </div>
      </main>
    </div>
  );
}
