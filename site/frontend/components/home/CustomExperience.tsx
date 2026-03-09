'use client';

import { motion } from 'framer-motion';
import { NeonButton } from '../ui/NeonButton';
import Image from 'next/image';

export function CustomExperience() {
    return (
        <section className="relative py-20 px-6 overflow-hidden min-h-[28rem]">
            <div className="relative z-10 max-w-7xl mx-auto">
                <motion.div
                    className="grid grid-cols-1 lg:grid-cols-2 gap-12 items-center"
                    initial={{ opacity: 0 }}
                    whileInView={{ opacity: 1 }}
                    viewport={{ once: true }}
                    transition={{ duration: 0.8 }}
                >
                    {/* Left: Image */}
                    <motion.div
                        className="order-1 lg:order-1 relative aspect-[4/5] rounded-[2.5rem] overflow-hidden"
                        initial={{ opacity: 0, x: -30 }}
                        whileInView={{ opacity: 1, x: 0 }}
                        viewport={{ once: true }}
                        transition={{ duration: 0.6, delay: 0.2 }}
                    >
                        <Image
                            src="/images/custom-order.png"
                            alt="Озвучка на заказ"
                            fill
                            className="object-cover"
                            unoptimized
                            sizes="(max-width: 1024px) 100vw, 50vw"
                        />
                        {/* Fade to black edges */}
                        <div className="absolute inset-0 bg-gradient-to-r from-[#000814] via-transparent to-[#000814]" />
                        <div className="absolute inset-0 bg-gradient-to-t from-[#000814] via-transparent to-transparent" />
                    </motion.div>

                    {/* Right: Text Content */}
                    <div className="order-2 lg:order-2">
                        <motion.div
                            initial={{ opacity: 0, x: 30 }}
                            whileInView={{ opacity: 1, x: 0 }}
                            viewport={{ once: true }}
                            transition={{ duration: 0.6, delay: 0.3 }}
                        >
                            <h2 className="font-heading text-4xl md:text-5xl font-bold text-white mb-6">
                                Озвучим твой сценарий
                            </h2>
                            <p className="text-zinc-400 text-lg leading-relaxed mb-8">
                                У тебя есть фантазия, которой нет в каталоге? Пришли нам свой текст. Мы озвучим его приятным голосом — именно с теми интонациями, которые ты хочешь услышать. Полная анонимность.
                            </p>
                            <NeonButton variant="primary" href="https://t.me/example" target="_blank">
                                Написать в Telegram
                            </NeonButton>
                        </motion.div>
                    </div>
                </motion.div>
            </div>
        </section>
    );
}
