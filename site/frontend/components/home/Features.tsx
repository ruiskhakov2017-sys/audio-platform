'use client';

import { motion } from 'framer-motion';
import { Headphones, Lock, Sparkles } from 'lucide-react';
import { GlassCard } from '../ui/GlassCard';

const features = [
    {
        icon: Headphones,
        title: 'Острее, чем видео',
        description: 'В видео ты просто зритель. В аудио — ты участник. Твое воображение дорисовывает то, что заводит именно тебя.'
    },
    {
        icon: Lock,
        title: 'Твой секрет',
        description: 'Экран телефона черный. Никто не увидит и не узнает, что ты слушаешь. Полная приватность в любом месте.'
    },
    {
        icon: Sparkles,
        title: 'Эффект присутствия',
        description: 'Шепот прямо в уши, дыхание, звуки. Ощущение, что все происходит с тобой в одной комнате.'
    }
];

export function Features() {
    return (
        <section className="relative py-20 px-6 overflow-hidden">
            <div className="relative z-10 max-w-7xl mx-auto">
                <motion.div
                    className="text-center mb-16"
                    initial={{ opacity: 0, y: 20 }}
                    whileInView={{ opacity: 1, y: 0 }}
                    viewport={{ once: true }}
                    transition={{ duration: 0.6 }}
                >
                    <h2 className="font-heading text-3xl md:text-4xl font-bold text-white mb-4">
                        Почему аудио?
                    </h2>
                </motion.div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
                    {features.map((feature, index) => (
                        <motion.div
                            key={feature.title}
                            initial={{ opacity: 0, y: 30 }}
                            whileInView={{ opacity: 1, y: 0 }}
                            viewport={{ once: true }}
                            transition={{ duration: 0.6, delay: index * 0.1 }}
                        >
                            <GlassCard className="p-8 h-full" animate>
                                <div className="w-16 h-16 rounded-[2rem] bg-[#00B4D8]/10 border border-[#00B4D8]/30 flex items-center justify-center mb-6">
                                    <feature.icon className="w-8 h-8 text-[#00B4D8]" strokeWidth={1.5} />
                                </div>
                                <h3 className="text-xl font-bold text-white mb-3">
                                    {feature.title}
                                </h3>
                                <p className="text-zinc-400 leading-relaxed">
                                    {feature.description}
                                </p>
                            </GlassCard>
                        </motion.div>
                    ))}
                </div>
            </div>
        </section>
    );
}
