import { resolve } from "node:path";
import { DEMO_IDS } from "../config/app-config";

export interface PortalMediaAsset {
  id: string;
  purpose: "housing_residence" | "student_club";
  storageKey: string;
  publicPath: string;
  localPath: string;
  sha256: string;
  altText: string;
  attribution: string;
  sourceUrl: string;
  licenseName: "Pexels License";
}

const publicMediaRoot = resolve(
  __dirname,
  "../../../web/public/media",
);
const storagePrefix = `tenants/${DEMO_IDS.tenantId}/portal-media`;

export const portalMediaAssets: readonly PortalMediaAsset[] = [
  {
    id: "70000000-0000-7000-8000-000000000101",
    purpose: "housing_residence",
    storageKey: `${storagePrefix}/housing/aster-residence-hall-room.jpg`,
    publicPath: "/media/housing/aster-residence-hall-room.jpg",
    localPath: resolve(publicMediaRoot, "housing/aster-residence-hall-room.jpg"),
    sha256: "5d72de56956d1cac2cac29d85d732a1fca8b9b86cf882a33f0be6b6d664fb05e",
    altText: "Bright shared room with two beds, wardrobes, and a window desk",
    attribution: "Photo by deno wang via Pexels",
    sourceUrl: "https://www.pexels.com/photo/two-beds-in-a-bedroom-11671086/",
    licenseName: "Pexels License",
  },
  {
    id: "70000000-0000-7000-8000-000000000102",
    purpose: "housing_residence",
    storageKey: `${storagePrefix}/housing/aster-apartments-room.jpg`,
    publicPath: "/media/housing/aster-apartments-room.jpg",
    localPath: resolve(publicMediaRoot, "housing/aster-apartments-room.jpg"),
    sha256: "a836c4fcaa9fd9a5926694a806a23a710048f27bba4454dfb138954ee2858449",
    altText: "Modern shared bedroom with twin beds and a large window",
    attribution: "Photo by Alan Antony via Pexels",
    sourceUrl: "https://www.pexels.com/photo/modern-bedroom-interior-18470955/",
    licenseName: "Pexels License",
  },
  {
    id: "70000000-0000-7000-8000-000000000103",
    purpose: "housing_residence",
    storageKey: `${storagePrefix}/housing/student-village-room.jpg`,
    publicPath: "/media/housing/student-village-room.jpg",
    localPath: resolve(publicMediaRoot, "housing/student-village-room.jpg"),
    sha256: "da850065779e2ad3f51a5ee748f939159215b87a684667a6fdceefe93fd3d7d3",
    altText: "Warm shared room with two beds, lamps, and neutral bedding",
    attribution: "Photo by Luis Zambrano via Pexels",
    sourceUrl: "https://www.pexels.com/photo/two-beds-in-bedroom-16436954/",
    licenseName: "Pexels License",
  },
  {
    id: "70000000-0000-7000-8000-000000000201",
    purpose: "student_club",
    storageKey: `${storagePrefix}/clubs/robotics.jpg`,
    publicPath: "/media/clubs/robotics.jpg",
    localPath: resolve(publicMediaRoot, "clubs/robotics.jpg"),
    sha256: "e5f1a809ff85bfb326bb098b1a9109cb68cce8c15314a4d8c336ee5cfaaa4296",
    altText: "Students collaborating on a robotics project in a workshop",
    attribution: "Photo by Vanessa Loring via Pexels",
    sourceUrl: "https://www.pexels.com/photo/young-students-doing-robotics-together-7869041/",
    licenseName: "Pexels License",
  },
  {
    id: "70000000-0000-7000-8000-000000000202",
    purpose: "student_club",
    storageKey: `${storagePrefix}/clubs/code-collective.jpg`,
    publicPath: "/media/clubs/code-collective.jpg",
    localPath: resolve(publicMediaRoot, "clubs/code-collective.jpg"),
    sha256: "9fc235b4d5662ff3e10f7e761f67c19cfb229efad60e578f951befc21ba49270",
    altText: "College students researching together around a library table",
    attribution: "Photo by Tima Miroshnichenko via Pexels",
    sourceUrl: "https://www.pexels.com/photo/college-students-studying-and-researching-6549913/",
    licenseName: "Pexels License",
  },
  {
    id: "70000000-0000-7000-8000-000000000203",
    purpose: "student_club",
    storageKey: `${storagePrefix}/clubs/women-in-business.jpg`,
    publicPath: "/media/clubs/women-in-business.jpg",
    localPath: resolve(publicMediaRoot, "clubs/women-in-business.jpg"),
    sha256: "d85e5278b3a53fcf7b9936dad89f29e30febb0f8ece128c8b0e4d0bb487fdeed",
    altText: "Women collaborating around documents during a business workshop",
    attribution: "Photo by RDNE Stock project via Pexels",
    sourceUrl: "https://www.pexels.com/photo/businesswomen-in-a-meeting-7648511/",
    licenseName: "Pexels License",
  },
  {
    id: "70000000-0000-7000-8000-000000000204",
    purpose: "student_club",
    storageKey: `${storagePrefix}/clubs/outdoor-aster.jpg`,
    publicPath: "/media/clubs/outdoor-aster.jpg",
    localPath: resolve(publicMediaRoot, "clubs/outdoor-aster.jpg"),
    sha256: "0cd40b90c9a7306af3909de672e45704399745a416cf72549f849fed2dde42c3",
    altText: "A group of friends hiking together on a forest trail",
    attribution: "Photo by Gustavo Denuncio via Pexels",
    sourceUrl: "https://www.pexels.com/photo/group-of-friends-hiking-in-forest-trail-30273507/",
    licenseName: "Pexels License",
  },
];
