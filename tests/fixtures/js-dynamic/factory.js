const registry = {};

export default {
  register(cmd) {
    registry[cmd.name] = cmd;
  },
  create(name) {
    return registry[name];
  },
};
