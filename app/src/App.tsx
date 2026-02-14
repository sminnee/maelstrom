import { useMaelstromData } from "./hooks/use-maelstrom-data";
import { AppLayout } from "./components/app-layout";

function App() {
  const { data, selectedProject, loading, error, refresh, selectProject } =
    useMaelstromData();

  return (
    <AppLayout
      projects={data?.projects ?? []}
      selectedProject={selectedProject}
      onSelectProject={selectProject}
      loading={loading}
      error={error}
      onRefresh={refresh}
    />
  );
}

export default App;
