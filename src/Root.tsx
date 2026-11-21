import { Composition } from "remotion";
import { CubeVideo } from "./CubeVideo";

export const RemotionRoot: React.FC = () => (
  <Composition
    id="CubeVideo"
    component={CubeVideo}
    durationInFrames={450}
    fps={30}
    width={1080}
    height={1920}
  />
);
