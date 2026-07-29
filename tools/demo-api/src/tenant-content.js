const harvardCourseSource = {
  label: "Harvard SEAS course listing",
  url: "https://seas.harvard.edu/computer-science/courses",
  dataStatus: "official_source",
};

const harvardProgramSource = {
  label: "Harvard SEAS Computer Science",
  url: "https://seas.harvard.edu/computer-science/bachelors-degree-computer-science",
  dataStatus: "official_source",
};

const harvardRequirementSource = {
  label: "Harvard CS concentration requirements",
  url: "https://csadvising.seas.harvard.edu/concentration/requirements/",
  dataStatus: "official_source",
};

const harvardClubSource = {
  label: "Harvard CS clubs and activities",
  url: "https://csadvising.seas.harvard.edu/opportunities/clubs/",
  dataStatus: "official_source",
};

const harvardCampusSource = {
  label: "Harvard College student activities",
  url: "https://college.harvard.edu/student-life/student-activities",
  dataStatus: "synthetic_preview",
};

const programmingResource = {
  id: "think-python-2e",
  title: "Think Python, 2nd Edition",
  description: "A beginner-friendly guide to programming and problem solving with Python.",
  url: "https://greenteapress.com/thinkpython2/thinkpython2.pdf",
  format: "pdf",
  provider: "Green Tea Press",
  licenseLabel: "CC BY-NC 3.0",
};

const discreteMathResource = {
  id: "mit-mathematics-for-computer-science",
  title: "Mathematics for Computer Science",
  description: "MIT's open undergraduate text on proofs, graphs, counting, and probability.",
  url: "https://ocw.mit.edu/courses/6-042j-mathematics-for-computer-science-spring-2015/mit6_042js15_textbook.pdf",
  format: "pdf",
  provider: "MIT OpenCourseWare",
  licenseLabel: "Creative Commons",
};

const algorithmsResource = {
  id: "erickson-algorithms",
  title: "Algorithms",
  description: "A rigorous open text covering recursion, dynamic programming, graphs, and complexity.",
  url: "https://jeffe.cs.illinois.edu/teaching/algorithms/book/Algorithms-JeffE.pdf",
  format: "pdf",
  provider: "Jeff Erickson · UIUC",
  licenseLabel: "CC BY 4.0",
};

const linearAlgebraResource = {
  id: "hefferon-linear-algebra",
  title: "Linear Algebra",
  description: "A free first-course text with worked examples, exercises, and applications.",
  url: "https://hefferon.net/linearalgebra/book.pdf",
  format: "pdf",
  provider: "Jim Hefferon",
  licenseLabel: "CC BY-SA 4.0",
};

export const harvardAcademicContent = Object.freeze({
  version: "HARVARD-CS-2026-27.preview.v1",
  courses: [
    {
      id: "11000000-0000-7000-8000-000000000020",
      code: "COMPSCI 20",
      title: "Discrete Mathematics for Computer Science",
      description:
        "Mathematical tools for computer science, including logic, set theory, combinatorics, probability, graph theory, and formal proof.",
      credits: 4,
      level: 20,
      availabilityLabel: "Spring 2027 published example",
      instructorNames: ["Adam Hesterberg"],
      meetingPattern: "Monday, Wednesday, Friday · 9:45–11:00 AM",
      source: harvardCourseSource,
      resources: [discreteMathResource],
      prerequisites: [],
    },
    {
      id: "11000000-0000-7000-8000-000000000032",
      code: "COMPSCI 32",
      title: "Computational Thinking and Problem Solving",
      description:
        "An introduction to computational thinking, problem solving, and programming with Python; prior programming experience is not required.",
      credits: 4,
      level: 32,
      availabilityLabel: "Spring 2027 published example",
      instructorNames: ["Michael Smith"],
      meetingPattern: "Monday, Wednesday · 1:30–2:45 PM",
      source: harvardCourseSource,
      resources: [programmingResource],
      prerequisites: [],
    },
    {
      id: "11000000-0000-7000-8000-000000000050",
      code: "COMPSCI 50",
      title: "Introduction to Computer Science",
      description:
        "Harvard's introduction to computer science and programming, emphasizing computational thinking, algorithms, data structures, correctness, design, and style.",
      credits: 4,
      level: 50,
      availabilityLabel: "Fall 2026 / Spring 2027 published example",
      instructorNames: ["David J. Malan", "Kelly Ding"],
      meetingPattern: "Multiple published lecture sections",
      source: harvardCourseSource,
      resources: [programmingResource],
      prerequisites: [],
    },
    {
      id: "11000000-0000-7000-8000-000000000051",
      code: "COMPSCI 51",
      title: "Abstraction and Design in Computation",
      description:
        "Program design through functional, imperative, and object-oriented paradigms, with software-engineering and computation models.",
      credits: 4,
      level: 51,
      availabilityLabel: "Fall 2026 published example",
      instructorNames: ["Stephen Chong"],
      meetingPattern: "Tuesday, Thursday · 11:15 AM–12:30 PM",
      source: harvardCourseSource,
      prerequisites: [],
    },
    {
      id: "11000000-0000-7000-8000-000000000061",
      code: "COMPSCI 61",
      title: "Systems Programming and Machine Organization",
      description:
        "Systems programming, data representation, storage, process management, synchronization, operating systems, and machine organization.",
      credits: 4,
      level: 61,
      availabilityLabel: "Fall 2026 / Spring 2027 published example",
      instructorNames: ["Eddie Kohler", "Juncheng Yang"],
      meetingPattern: "Published fall and spring sections",
      source: harvardCourseSource,
      prerequisites: [],
    },
    {
      id: "11000000-0000-7000-8000-000000000079",
      code: "COMPSCI 79",
      title: "Design of Useful and Usable Interactive Systems",
      description:
        "Human-centered product design, need finding, creativity practices, prototyping, and evaluation of interactive systems.",
      credits: 4,
      level: 79,
      availabilityLabel: "Fall 2026 published example",
      instructorNames: ["Krzysztof Gajos"],
      meetingPattern: "Tuesday, Thursday · 9:45–11:00 AM",
      source: harvardCourseSource,
      prerequisites: [],
    },
    {
      id: "11000000-0000-7000-8000-000000001200",
      code: "COMPSCI 1200",
      title: "Introduction to Algorithms and Their Limitations",
      description:
        "A tenant-seeded plan example for the formal-reasoning, algorithms, and computational-limitations areas of the CS concentration.",
      credits: 4,
      level: 120,
      availabilityLabel: "Plan example · verify current offering",
      instructorNames: [],
      meetingPattern: null,
      source: harvardRequirementSource,
      resources: [discreteMathResource, algorithmsResource],
      prerequisites: [],
    },
    {
      id: "11000000-0000-7000-8000-000000001240",
      code: "COMPSCI 1240",
      title: "Data Structures and Algorithms",
      description:
        "A tenant-seeded advanced plan example associated with Harvard's algorithms, formal-reasoning, and advanced-computer-science requirement tags.",
      credits: 4,
      level: 124,
      availabilityLabel: "Plan example · verify current offering",
      instructorNames: [],
      meetingPattern: null,
      source: harvardRequirementSource,
      resources: [algorithmsResource],
      prerequisites: [],
    },
    {
      id: "11000000-0000-7000-8000-000000000110",
      code: "STAT 110",
      title: "Introduction to Probability",
      description:
        "A probability-course option named in the published Harvard CS concentration requirements.",
      credits: 4,
      level: 110,
      availabilityLabel: "Requirement option · verify current offering",
      instructorNames: [],
      meetingPattern: null,
      source: harvardRequirementSource,
      resources: [linearAlgebraResource],
      prerequisites: [],
    },
    {
      id: "11000000-0000-7000-8000-000000000021",
      code: "MATH 21B",
      title: "Linear Algebra and Differential Equations",
      description:
        "A linear-algebra option named in the published Harvard CS concentration requirements.",
      credits: 4,
      level: 21,
      availabilityLabel: "Requirement option · verify current offering",
      instructorNames: [],
      meetingPattern: null,
      source: harvardRequirementSource,
      prerequisites: [],
    },
  ],
  programs: [
    {
      id: "21000000-0000-7000-8000-000000000101",
      code: "AB-CS",
      name: "Computer Science Concentration",
      degree: "Bachelor of Arts",
      totalCredits: 128,
      description:
        "A liberal-arts computer science pathway combining mathematical preparation with programming, formal reasoning, systems, computation and the world, and advanced computer science.",
      source: harvardProgramSource,
      requirements: [
        ["COMPSCI 50", "major_core", 1],
        ["MATH 21B", "math_science", 1],
        ["COMPSCI 20", "major_core", 2],
        ["STAT 110", "math_science", 2],
        ["COMPSCI 51", "major_core", 3],
        ["COMPSCI 61", "major_core", 3],
        ["COMPSCI 79", "major_core", 4],
        ["COMPSCI 1200", "major_core", 4],
        ["COMPSCI 1240", "major_core", 5],
      ],
    },
  ],
});

export const harvardCampusLifeContent = Object.freeze({
  events: [
    {
      id: "52000000-0000-7000-8000-000000000101",
      title: "Student Organization Fair",
      description:
        "A synthetic 2027 preview of Harvard's annual first-week organization fair, where students explore hundreds of groups and communities.",
      startsAt: "2027-09-03T19:00:00.000Z",
      endsAt: "2027-09-03T22:00:00.000Z",
      location: "Tercentenary Theatre",
      category: "social",
      featured: true,
      accent: "coral",
      visualTheme: "festival",
      imageUrl: "/media/events/welcome-week-block-party.webp",
      imageAlt:
        "Students enjoying music, food stalls, and conversation at a welcome-week block party on a campus lawn",
      imageAttribution: "Original portal artwork generated with OpenAI",
      imageSourceUrl: null,
      source: {
        label: "Harvard College Student Organization Fair",
        url: "https://dso.college.harvard.edu/student-involvement-fair",
        dataStatus: "synthetic_preview",
      },
      registrationUrl: null,
    },
    {
      id: "52000000-0000-7000-8000-000000000102",
      title: "CS Community Project Night",
      description:
        "A synthetic preview bringing computing clubs together for demonstrations, project matching, and conversations with student leaders.",
      startsAt: "2027-09-08T22:00:00.000Z",
      endsAt: "2027-09-09T00:00:00.000Z",
      location: "Science and Engineering Complex",
      category: "academic",
      featured: true,
      accent: "blue",
      visualTheme: "discovery",
      imageUrl: "/media/events/first-year-research-showcase.webp",
      imageAlt:
        "Students presenting robotics projects and research posters in a university innovation hall",
      imageAttribution: "Original portal artwork generated with OpenAI",
      imageSourceUrl: null,
      source: {
        ...harvardClubSource,
        dataStatus: "synthetic_preview",
      },
      registrationUrl: null,
    },
    {
      id: "52000000-0000-7000-8000-000000000103",
      title: "Global Day of Service Meetup",
      description:
        "A synthetic orientation preview inspired by Harvard's service and leadership programming in Cambridge and Boston.",
      startsAt: "2027-09-11T14:00:00.000Z",
      endsAt: "2027-09-11T18:00:00.000Z",
      location: "Harvard Yard",
      category: "social",
      featured: true,
      accent: "gold",
      visualTheme: "community",
      imageUrl: "/media/clubs/outdoor-aster.jpg",
      imageAlt: "Students hiking together on a wooded trail",
      imageAttribution: "Photo by Gustavo Denuncio via Pexels",
      imageSourceUrl:
        "https://www.pexels.com/photo/group-of-friends-hiking-in-forest-trail-30273507/",
      source: harvardCampusSource,
      registrationUrl: null,
    },
  ],
  clubs: [
    {
      id: "53000000-0000-7000-8000-000000000101",
      name: "Harvard Computer Society",
      category: "Computing",
      description:
        "A no-competition community connecting students with technical projects, learning, mentorship, and career opportunities.",
      contactName: "Student leadership team",
      contactRole: "Harvard Computer Society",
      contactChannel: "presidents@hcs.harvard.edu",
      latestUpdate: "Explore HCS programs and affiliated project groups.",
      nextActivity: "Project community meetup · preview",
      imageUrl: "/media/clubs/code-collective.jpg",
      imageAlt: "College students researching together around a library table",
      imageAttribution: "Photo by Tima Miroshnichenko via Pexels",
      imageSourceUrl:
        "https://www.pexels.com/photo/college-students-studying-and-researching-6549913/",
      source: harvardClubSource,
      socialLinks: [
        { label: "Website", url: "https://hcs.harvard.edu/" },
        {
          label: "Instagram",
          url: "https://www.instagram.com/harvard.computer.society/",
        },
      ],
    },
    {
      id: "53000000-0000-7000-8000-000000000102",
      name: "Women in Computer Science",
      category: "Community & Technology",
      description:
        "A student community supporting technical women at Harvard and beyond through connection, learning, and professional development.",
      contactName: "Student leadership team",
      contactRole: "Harvard WiCS",
      contactChannel: "harvardwics@gmail.com",
      latestUpdate: "Community and mentorship programming is highlighted for new members.",
      nextActivity: "Welcome circle · preview",
      imageUrl: "/media/clubs/women-in-business.jpg",
      imageAlt: "Women collaborating around documents during a workshop",
      imageAttribution: "Photo by RDNE Stock project via Pexels",
      imageSourceUrl:
        "https://www.pexels.com/photo/businesswomen-in-a-meeting-7648511/",
      source: harvardClubSource,
      socialLinks: [{ label: "Website", url: "https://www.harvardwics.com/" }],
    },
    {
      id: "53000000-0000-7000-8000-000000000103",
      name: "Harvard Undergraduate Robotics Club",
      category: "Engineering & Robotics",
      description:
        "Hands-on robotics projects paired with a welcoming undergraduate engineering community.",
      contactName: "Student leadership team",
      contactRole: "Robotics Club",
      contactChannel: "team@harvardrobotics.com",
      latestUpdate: "Project teams are preparing demonstrations for incoming students.",
      nextActivity: "Open build session · preview",
      imageUrl: "/media/clubs/robotics.jpg",
      imageAlt: "Students collaborating on a robotics project in a workshop",
      imageAttribution: "Photo by Vanessa Loring via Pexels",
      imageSourceUrl:
        "https://www.pexels.com/photo/young-students-doing-robotics-together-7869041/",
      source: harvardClubSource,
      socialLinks: [{ label: "Website", url: "https://harvardrobotics.com/" }],
    },
    {
      id: "53000000-0000-7000-8000-000000000104",
      name: "Harvard Open Data Project",
      category: "Data & Public Interest",
      description:
        "Students and faculty use public Harvard data, analysis, and visualization to improve transparency and understand campus questions.",
      contactName: "Student leadership team",
      contactRole: "Open Data Project",
      contactChannel: "team@hodp.org",
      latestUpdate: "New contributors can explore published campus-data projects.",
      nextActivity: "Data story workshop · preview",
      imageUrl: "/media/clubs/code-collective.jpg",
      imageAlt: "Students collaborating around laptops in a library",
      imageAttribution: "Photo by Tima Miroshnichenko via Pexels",
      imageSourceUrl:
        "https://www.pexels.com/photo/college-students-studying-and-researching-6549913/",
      source: harvardClubSource,
      socialLinks: [
        { label: "Website", url: "https://hodp.org/" },
        { label: "Harvard on Facebook", url: "https://www.facebook.com/Harvard/" },
      ],
    },
  ],
});
