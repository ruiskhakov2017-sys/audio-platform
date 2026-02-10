'use client';

import { motion } from 'framer-motion';
import { ReactNode } from 'react';

interface GlassCardProps {
    children: ReactNode;
    className?: string;
    variant?: 'default' | 'strong';
    animate?: boolean;
}

export function GlassCard({
    children,
    className = '',
    variant = 'default',
    animate = false
}: GlassCardProps) {
    const baseClasses = variant === 'strong' ? 'glass-premium' : 'glass';

    const Component = animate ? motion.div : 'div';

    return (
        <Component
            className={`${baseClasses} rounded-[2.5rem] ${className}`}
            {...(animate && {
                whileHover: { scale: 1.02 },
                transition: { duration: 0.3 }
            })}
        >
            {children}
        </Component>
    );
}
