import { ThreeCanvas } from "@remotion/three";
import React, { useMemo } from "react";
import * as THREE from "three";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

const CYAN = "#00e5ff";
const MAGENTA = "#ff2bd6";

const makeGlowTexture = () => {
  const c = document.createElement("canvas");
  c.width = c.height = 256;
  const g = c.getContext("2d")!;
  const grad = g.createRadialGradient(128, 128, 0, 128, 128, 128);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.25, "rgba(255,255,255,0.45)");
  grad.addColorStop(0.6, "rgba(255,255,255,0.08)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 256, 256);
  return new THREE.CanvasTexture(c);
};

// 12 edges of a cube of size 2, as [position, rotation, length]
const EDGE_DEFS: { pos: [number, number, number]; axis: "x" | "y" | "z" }[] = [];
for (const a of [-1, 1]) {
  for (const b of [-1, 1]) {
    EDGE_DEFS.push({ pos: [0, a, b], axis: "x" });
    EDGE_DEFS.push({ pos: [a, 0, b], axis: "y" });
    EDGE_DEFS.push({ pos: [a, b, 0], axis: "z" });
  }
}

const GlowingCube: React.FC<{ frame: number; fps: number }> = ({ frame, fps }) => {
  const t = frame / fps;
  const entrance = spring({ frame, fps, config: { damping: 12, mass: 0.8 } });
  const pulse = 1 + Math.sin(t * Math.PI * 1.5) * 0.04;
  const scale = entrance * pulse;
  const glowTex = useMemo(() => makeGlowTexture(), []);
  const hue = 0.5 + 0.35 * (0.5 + 0.5 * Math.sin(t * 0.8));
  const edgeColor = new THREE.Color().setHSL(hue, 1, 0.5);
  const y = 1.1 + Math.sin(t * 2) * 0.12;

  return (
    <group position={[0, y, 0]}>
      {/* Soft halo behind the cube */}
      <sprite scale={[7 * scale * pulse, 7 * scale * pulse, 1]} position={[0, 0, -1.5]}>
        <spriteMaterial
          map={glowTex}
          color={edgeColor}
          transparent
          opacity={0.9}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </sprite>
      <group rotation={[t * 0.7, t * 1.1, t * 0.3]} scale={scale}>
        {/* Solid core */}
        <mesh>
          <boxGeometry args={[1.94, 1.94, 1.94]} />
          <meshStandardMaterial
            color="#0b1636"
            emissive="#0a3a66"
            emissiveIntensity={0.6}
            metalness={0.7}
            roughness={0.3}
          />
        </mesh>
        {/* Neon edge tubes + their glow */}
        {EDGE_DEFS.map(({ pos, axis }, i) => {
          const rot: [number, number, number] =
            axis === "x" ? [0, 0, Math.PI / 2] : axis === "z" ? [Math.PI / 2, 0, 0] : [0, 0, 0];
          return (
            <group key={i} position={pos} rotation={rot}>
              <mesh>
                <cylinderGeometry args={[0.035, 0.035, 2.07, 12]} />
                <meshBasicMaterial color={edgeColor} toneMapped={false} />
              </mesh>
              <mesh>
                <cylinderGeometry args={[0.11, 0.11, 2.15, 12]} />
                <meshBasicMaterial
                  color={edgeColor}
                  transparent
                  opacity={0.35}
                  blending={THREE.AdditiveBlending}
                  depthWrite={false}
                />
              </mesh>
            </group>
          );
        })}
        {/* Corner sparks */}
        {[-1, 1].flatMap((a) =>
          [-1, 1].flatMap((b) =>
            [-1, 1].map((c) => (
              <sprite key={`${a}${b}${c}`} position={[a, b, c]} scale={[0.6, 0.6, 1]}>
                <spriteMaterial
                  map={glowTex}
                  color="#ffffff"
                  transparent
                  blending={THREE.AdditiveBlending}
                  depthWrite={false}
                />
              </sprite>
            ))
          )
        )}
      </group>
    </group>
  );
};

const Particles: React.FC<{ frame: number }> = ({ frame }) => {
  const positions = useMemo(() => {
    const arr = new Float32Array(600 * 3);
    let seed = 42;
    const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    for (let i = 0; i < 600; i++) {
      arr[i * 3] = (rnd() - 0.5) * 14;
      arr[i * 3 + 1] = (rnd() - 0.5) * 24;
      arr[i * 3 + 2] = (rnd() - 0.5) * 10 - 3;
    }
    return arr;
  }, []);
  return (
    <points rotation={[0, frame * 0.002, 0]}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial size={0.05} color="#8fdcff" transparent opacity={0.7} />
    </points>
  );
};

export const CubeVideo: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps, width, height, durationInFrames } = useVideoConfig();

  const textIn = spring({ frame: frame - 20, fps, config: { damping: 14 } });
  const fadeOut = interpolate(frame, [durationInFrames - 20, durationInFrames], [1, 0], {
    extrapolateLeft: "clamp",
  });
  const glow = 30 + Math.sin(frame / 8) * 12;

  return (
    <AbsoluteFill
      style={{
        background: "radial-gradient(circle at 50% 40%, #0d1633 0%, #05060f 55%, #000 100%)",
        opacity: fadeOut,
      }}
    >
      <ThreeCanvas width={width} height={height} camera={{ position: [0, 0, 9], fov: 55 }}>
        <ambientLight intensity={0.3} />
        <pointLight position={[4, 4, 5]} intensity={60} color={CYAN} />
        <pointLight position={[-4, -3, 4]} intensity={50} color={MAGENTA} />
        <Particles frame={frame} />
        <GlowingCube frame={frame} fps={fps} />
      </ThreeCanvas>

      <AbsoluteFill
        style={{
          justifyContent: "flex-end",
          alignItems: "center",
          paddingBottom: 300,
        }}
      >
        <div
          style={{
            fontFamily: "Impact, 'Arial Black', 'Helvetica Neue', sans-serif",
            fontWeight: 900,
            fontSize: 150,
            lineHeight: 1.0,
            textAlign: "center",
            color: "#ffffff",
            letterSpacing: 4,
            textTransform: "uppercase",
            transform: `translateY(${(1 - textIn) * 200}px) scale(${0.8 + textIn * 0.2})`,
            opacity: textIn,
            textShadow: `0 0 ${glow}px ${CYAN}, 0 0 ${glow * 2}px ${MAGENTA}, 0 8px 0 #000`,
            WebkitTextStroke: "3px #00e5ff",
          }}
        >
          MY FIRST
          <br />
          AI VIDEO
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
