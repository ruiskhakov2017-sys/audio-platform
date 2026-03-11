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

      <main className="relative z-10 pt-28 pb-24 px-4 md:px-6">
        <div className="max-w-4xl mx-auto py-16 flex flex-col gap-10">
          {/* Главный заголовок (Слоган) */}
          <motion.h1
            className="text-4xl md:text-5xl lg:text-6xl font-black text-center bg-clip-text text-transparent bg-gradient-to-r from-[#00B4D8] to-blue-600 mb-8 leading-tight"
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6 }}
          >
            Dirty Secrets — Твои фантазии обретают голос
          </motion.h1>

          {/* Вступление */}
          <motion.div
            className="relative overflow-hidden bg-white/[0.02] backdrop-blur-xl border border-white/10 rounded-3xl p-8 md:p-12 group transition-all duration-500 hover:border-[#00B4D8]/30 hover:shadow-[0_0_40px_rgba(0,180,216,0.1)]"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-50px' }}
            transition={{ duration: 0.5 }}
          >
            <div className="absolute left-0 top-0 bottom-0 w-1 bg-gradient-to-b from-[#00B4D8] to-transparent opacity-50" />
            <span className="text-[#00B4D8] text-sm md:text-base font-bold uppercase tracking-[0.2em] mb-6 block text-left">
              Вступление
            </span>
            <p className="text-white/80 text-lg leading-relaxed font-light text-left">
              Мы создали Dirty Secrets для тех, кто понимает: самое мощное воображение — у нас в голове. В мире, перенасыщенном визуальным шумом, мы решили вернуть интимность. Звук не диктует тебе, что видеть. Он лишь направляет, позволяя твоему разуму дорисовывать самые сокровенные детали.
            </p>
          </motion.div>

          {/* Наша философия */}
          <motion.div
            className="relative overflow-hidden bg-white/[0.02] backdrop-blur-xl border border-white/10 rounded-3xl p-8 md:p-12 group transition-all duration-500 hover:border-[#00B4D8]/30 hover:shadow-[0_0_40px_rgba(0,180,216,0.1)]"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-50px' }}
            transition={{ duration: 0.5 }}
          >
            <div className="absolute left-0 top-0 bottom-0 w-1 bg-gradient-to-b from-[#00B4D8] to-transparent opacity-50" />
            <span className="text-[#00B4D8] text-sm md:text-base font-bold uppercase tracking-[0.2em] mb-6 block text-left">
              Наша философия
            </span>
            <p className="text-white/80 text-lg leading-relaxed font-light text-left">
              Dirty Secrets — это территория свободы от стереотипов. Мы верим, что каждый заслуживает качественный контент, который заставляет сердце биться чаще. Здесь нет места дешевым приемам — только эстетика, атмосфера и честные эмоции.
            </p>
          </motion.div>

          {/* Почему именно мы */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {whyUs.map((item, i) => (
              <motion.div
                key={item.title}
                className="relative overflow-hidden bg-white/[0.02] backdrop-blur-xl border border-white/10 rounded-3xl p-8 group transition-all duration-500 hover:border-[#00B4D8]/30 hover:shadow-[0_0_40px_rgba(0,180,216,0.1)] flex flex-col"
                initial={{ opacity: 0, y: 24 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ duration: 0.5, delay: i * 0.1 }}
              >
                <div className="absolute left-0 top-0 bottom-0 w-1 bg-gradient-to-b from-[#00B4D8] to-transparent opacity-50" />
                <div
                  className="w-12 h-12 rounded-xl flex items-center justify-center mb-6 shrink-0 bg-[#00B4D8]/10 border border-[#00B4D8]/20"
                >
                  <item.icon className="w-6 h-6 text-[#00B4D8]" strokeWidth={1.5} />
                </div>
                <h3 className="text-lg font-bold text-white mb-4 text-left">{item.title}</h3>
                <p className="text-white/80 text-sm leading-relaxed font-light text-left flex-1">{item.text}</p>
              </motion.div>
            ))}
          </div>

          {/* Наш подход */}
          <motion.div
            className="relative overflow-hidden bg-white/[0.02] backdrop-blur-xl border border-white/10 rounded-3xl p-8 md:p-12 group transition-all duration-500 hover:border-[#00B4D8]/30 hover:shadow-[0_0_40px_rgba(0,180,216,0.1)]"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-50px' }}
            transition={{ duration: 0.5 }}
          >
            <div className="absolute left-0 top-0 bottom-0 w-1 bg-gradient-to-b from-[#00B4D8] to-transparent opacity-50" />
            <span className="text-[#00B4D8] text-sm md:text-base font-bold uppercase tracking-[0.2em] mb-6 block text-left">
              Наш подход
            </span>
            <p className="text-white/80 text-lg leading-relaxed font-light text-left mb-6">
              Мы ценим твое время и твой выбор. Dirty Secrets — это полностью независимый проект. Мы развиваемся вместе с нашими слушателями и всегда открыты к вашим идеям. Здесь ты не просто пользователь, ты — часть закрытого клуба любителей качественной аудио-литературы.
            </p>
            <p className="text-zinc-400 text-lg leading-relaxed italic text-left">
              Закрой глаза. Нажми на Play. Позволь себе услышать то, о чем другие только шепчутся.
            </p>
          </motion.div>
        </div>
      </main>
    </div>
  );
}
