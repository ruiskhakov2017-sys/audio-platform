'use client';

import { motion } from 'framer-motion';
import { useMemo, useState, useEffect } from 'react';

interface NeonWaveformProps {
    className?: string;
    color?: string;
    animate?: boolean;
}

export function NeonWaveform({
    className = '',
    color = '#00B4D8',
    animate = true
}: NeonWaveformProps) {
    // Only render on client to avoid hydration mismatch with framer-motion
    const [isMounted, setIsMounted] = useState(false);

    useEffect(() => {
        setIsMounted(true);
    }, []);

    // Generate wave path - memoized to prevent recalculation
    const wavePath = useMemo(() => {
        const points = 100;
        const amplitude = 30;
        const frequency = 0.02;
        let path = 'M 0 50';

        for (let i = 0; i <= points * 2; i++) {
            const x = (i / points) * 100;
            const y = 50 + Math.sin(i * frequency * Math.PI) * amplitude;
            path += ` L ${x} ${y}`;
        }

        return path;
    }, []);

    // Don't render SVG until mounted on client
    if (!isMounted) {
        return <div className={`relative overflow-hidden ${className}`} />;
    }

    return (
        <div className={`relative overflow-hidden ${className}`}>
            <svg
                className="absolute inset-0 w-[200%] h-full"
                viewBox="0 0 200 100"
                preserveAspectRatio="none"
            >
                <motion.path
                    d={wavePath}
                    fill="none"
                    stroke={color}
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    opacity={0.4}
                    {...(animate && {
                        animate: {
                            x: [0, -100]
                        },
                        transition: {
                            duration: 8,
                            repeat: Infinity,
                            ease: 'linear'
                        }
                    })}
                />
                <motion.path
                    d={wavePath}
                    fill="none"
                    stroke={color}
                    strokeWidth="2"
                    strokeLinecap="round"
                    opacity={0.6}
                    style={{ transform: 'translateY(10px)' }}
                    {...(animate && {
                        animate: {
                            x: [0, -100]
                        },
                        transition: {
                            duration: 10,
                            repeat: Infinity,
                            ease: 'linear'
                        }
                    })}
                />
            </svg>
        </div>
    );
}
