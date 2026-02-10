'use client';

import { motion } from 'framer-motion';
import { NeonWaveform } from '../ui/NeonWaveform';
import { NeonButton } from '../ui/NeonButton';

export function Hero() {
    return (
        <section className="relative min-h-screen flex items-center justify-center overflow-hidden bg-[#000814]">
            {/* Фоновое изображение (силуэт) — сочное, хорошо видно */}
            <div
                className="absolute inset-0 bg-cover bg-center bg-no-repeat opacity-90 pointer-events-none"
                style={{ backgroundImage: 'url(/hero-bg.png)' }}
                aria-hidden
            />
            {/* Градиент: низ чёрный (читаемость текста), верх светлее (силуэт проступает) */}
            <div className="absolute inset-0 bg-gradient-to-b from-black/60 via-black/70 to-black/95 pointer-events-none" aria-hidden />

            {/* Ambient Background Orbs */}
            <div className="absolute inset-0 overflow-hidden pointer-events-none">
                <motion.div
                    className="absolute top-[-10%] left-[-10%] w-[500px] h-[500px] rounded-full bg-cyan-900/20 blur-[100px]"
                    animate={{
                        x: [0, 50, 0],
                        y: [0, 30, 0],
                        scale: [1, 1.1, 1]
                    }}
                    transition={{ duration: 20, repeat: Infinity, ease: "easeInOut" }}
                />
                <motion.div
                    className="absolute bottom-[-10%] right-[-10%] w-[600px] h-[600px] rounded-full bg-blue-900/20 blur-[120px]"
                    animate={{
                        x: [0, -50, 0],
                        y: [0, -30, 0],
                        scale: [1, 1.2, 1]
                    }}
                    transition={{ duration: 25, repeat: Infinity, ease: "easeInOut" }}
                />
            </div>

            {/* Floating Particles (Marine Snow) */}
            <div className="absolute inset-0 pointer-events-none">
                {[...Array(20)].map((_, i) => (
                    <motion.div
                        key={i}
                        className="absolute w-1 h-1 bg-cyan-500/20 rounded-full"
                        style={{
                            top: `${Math.random() * 100}%`,
                            left: `${Math.random() * 100}%`,
                        }}
                        animate={{
                            y: [0, -100, 0],
                            opacity: [0, 1, 0],
                        }}
                        transition={{
                            duration: Math.random() * 10 + 10,
                            repeat: Infinity,
                            ease: "linear",
                            delay: Math.random() * 5
                        }}
                    />
                ))}
            </div>

            {/* Animated Waveform Background */}
            <div className="absolute inset-0 opacity-30 mix-blend-screen">
                <NeonWaveform className="h-full" />
            </div>

            {/* Content */}
            <div className="relative z-10 text-center px-6 max-w-6xl mx-auto">
                <motion.div
                    initial={{ opacity: 0, scale: 0.9 }}
                    animate={{ opacity: 1, scale: 1 }}
                    transition={{ duration: 1 }}
                    className="absolute -inset-20 bg-cyan-500/5 blur-3xl rounded-full pointer-events-none"
                />

                <motion.h1
                    className="font-heading text-6xl md:text-7xl lg:text-8xl font-semibold mb-8 leading-tight relative drop-shadow-2xl"
                    initial={{ opacity: 0, y: 30 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.8 }}
                >
                    <span className="text-white drop-shadow-2xl">
                        Твои скрытые{' '}
                    </span>
                    <span className="text-[#00B4D8] drop-shadow-[0_0_15px_rgba(0,180,216,0.5)]">
                        фантазии
                    </span>
                    <br />
                    <span className="text-white drop-shadow-2xl">
                        теперь в звуке
                    </span>
                </motion.h1>

                <motion.p
                    className="text-xl md:text-2xl text-zinc-300 mb-14 max-w-3xl mx-auto leading-relaxed drop-shadow-md"
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.8, delay: 0.2 }}
                >
                    Коллекция откровенных аудиорассказов. Закрой глаза и позволь воображению нарисовать то, что видео никогда не покажет. Анонимно. Чувственно. Только для тебя.
                </motion.p>

                <motion.div
                    className="flex flex-col sm:flex-row items-center justify-center gap-5"
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.8, delay: 0.4 }}
                >
                    <NeonButton variant="primary" size="large" href="/browse">
                        Слушать истории
                    </NeonButton>
                    <NeonButton variant="glass" size="large" href="#genres">
                        Выбрать жанр
                    </NeonButton>
                </motion.div>
            </div>

            {/* Gradient Overlay Bottom */}
            <div className="absolute bottom-0 left-0 right-0 h-40 bg-gradient-to-t from-[#000814] to-transparent pointer-events-none" />
        </section>
    );
}
