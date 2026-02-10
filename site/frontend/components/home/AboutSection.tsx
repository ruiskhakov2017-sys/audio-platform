'use client';

import { motion } from 'framer-motion';
import Image from 'next/image';
import { GlassCard } from '../ui/GlassCard';

export function AboutSection() {
    return (
        <section id="about" className="relative py-20 px-6 overflow-hidden">
            <div className="absolute inset-0 z-0">
                <Image
                    src="/bg2.png"
                    alt="background"
                    fill
                    className="object-cover w-full h-full opacity-10 grayscale mix-blend-screen"
                    quality={50}
                    unoptimized
                />
            </div>
            <div className="relative z-10 max-w-6xl mx-auto">
                <motion.div
                    initial={{ opacity: 0, y: 30 }}
                    whileInView={{ opacity: 1, y: 0 }}
                    viewport={{ once: true }}
                    transition={{ duration: 0.8 }}
                >
                    <GlassCard className="p-12" variant="strong">
                        <h2 className="font-heading text-3xl md:text-5xl font-bold text-white mb-6">
                            Расслабься по-новому
                        </h2>
                        <p className="text-zinc-400 text-lg leading-relaxed">
                            Устаешь от бесконечных картинок и экранов? Закрой глаза. Аудио-истории — это лучший способ переключить мозг, снять стресс и погрузиться в свои желания. Без сложных сюжетов, просто чистые эмоции.
                        </p>
                    </GlassCard>
                </motion.div>
            </div>
        </section>
    );
}
