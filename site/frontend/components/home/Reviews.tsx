'use client';

import { motion } from 'framer-motion';
import { Star } from 'lucide-react';
import { GlassCard } from '../ui/GlassCard';

const reviews = [
    {
        text: 'Сначала стеснялась, но это реально затягивает. Голос в «Ночном госте» просто вау.',
        author: 'Алина, 24',
    },
    {
        text: 'Отличная альтернатива видео. Можно слушать в машине или перед сном. Качество звука топ.',
        author: 'Алекс',
    },
    {
        text: 'Заказал озвучку своей фантазии. Сделали быстро и анонимно. Жена в восторге.',
        author: 'Аноним',
    },
];

export function Reviews() {
    return (
        <section className="py-20 px-6">
            <div className="max-w-7xl mx-auto">
                <motion.h2
                    className="font-heading text-3xl md:text-4xl font-bold text-white text-center mb-12"
                    initial={{ opacity: 0, y: 20 }}
                    whileInView={{ opacity: 1, y: 0 }}
                    viewport={{ once: true }}
                    transition={{ duration: 0.6 }}
                >
                    Что говорят слушатели
                </motion.h2>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
                    {reviews.map((review, index) => (
                        <motion.div
                            key={review.author}
                            initial={{ opacity: 0, y: 20 }}
                            whileInView={{ opacity: 1, y: 0 }}
                            viewport={{ once: true }}
                            transition={{ duration: 0.5, delay: index * 0.1 }}
                        >
                            <GlassCard className="p-8 h-full">
                                <div className="flex gap-1 mb-4">
                                    {[...Array(5)].map((_, i) => (
                                        <Star key={i} className="w-5 h-5 text-amber-400 fill-amber-400" strokeWidth={0} />
                                    ))}
                                </div>
                                <p className="text-zinc-400 italic leading-relaxed mb-4">
                                    «{review.text}»
                                </p>
                                <p className="text-zinc-500 text-sm">
                                    — {review.author}
                                </p>
                            </GlassCard>
                        </motion.div>
                    ))}
                </div>
            </div>
        </section>
    );
}
